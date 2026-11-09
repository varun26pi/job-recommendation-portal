#!/usr/bin/env python3
"""
search_jobs.py: discover direct job-posting links on public ATS platforms via
Google Search (Serper API) and insert them into the Supabase `jobs` table.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit, urlunsplit
import requests
from supabase import Client, create_client

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
MAX_ROLES_PER_USER = int(os.getenv("MAX_ROLES_PER_USER", "3"))
MAX_DESCRIPTION_CHARS = 1000
MIN_RELEVANCE = 0.5

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("search_jobs")

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

@dataclass(frozen=True)
class Source:
    name: str
    sites: tuple[str, ...]
    hosts: tuple[str, ...]
    path_re: re.Pattern[str]
    company_from_subdomain: bool = False

SOURCES: tuple[Source, ...] = (
    Source("Greenhouse", ("boards.greenhouse.io", "job-boards.greenhouse.io"), ("boards.greenhouse.io", "job-boards.greenhouse.io"), re.compile(r"^/(?P<company>[\w-]+)/jobs/\d+$")),
    Source("Lever", ("jobs.lever.co",), ("jobs.lever.co", "jobs.eu.lever.co"), re.compile(rf"^/(?P<company>[\w.-]+)/{UUID}$", re.I)),
    Source("Ashby", ("jobs.ashbyhq.com",), ("jobs.ashbyhq.com",), re.compile(rf"^/(?P<company>[\w.%-]+)/{UUID}$", re.I)),
    Source("Workable", ("apply.workable.com",), ("apply.workable.com",), re.compile(r"^/(?P<company>[\w-]+)/j/[A-Za-z0-9]+$")),
    Source("Naukri", ("naukri.com",), ("naukri.com",), re.compile(r"^/job-listings-[\w-]+$")),
    Source("Workday", ("myworkdayjobs.com",), ("myworkdayjobs.com",), re.compile(r"^(?:/[a-z]{2}-[A-Za-z]{2})?/[\w-]+/job/.+$"), company_from_subdomain=True),
)

@dataclass(frozen=True)
class JobLink:
    source: Source
    company: str | None
    url: str

def _prettify(slug: str | None) -> str | None:
    if not slug: return None
    return unquote(slug).replace("-", " ").replace("_", " ").strip().title() or None

def classify(url: str) -> JobLink | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None

    host = parts.hostname.lower()
    path = re.sub(r"/apply$", "", parts.path.rstrip("/"))

    for src in SOURCES:
        if not any(host == h or host.endswith("." + h) for h in src.hosts): continue
        match = src.path_re.match(path)
        if not match: continue
        slug = host.split(".")[0] if src.company_from_subdomain else match.groupdict().get("company")
        return JobLink(src, _prettify(slug), urlunsplit(("https", host, path, "", "")))
    return None

_GREENHOUSE_TITLE = re.compile(r"^job application for\s+(?P<role>.+?)\s+at\s+(?P<company>.+)$", re.I)
_PLATFORM_SUFFIX = re.compile(r"\s*[|\-–]\s*(naukri\.com|lever|greenhouse|workable|ashby|workday)\s*$", re.I)
_STOPWORDS = {"and", "the", "for", "of", "in", "a", "an", "to", "with"}

def clean_title(raw: str, link: JobLink) -> tuple[str, str]:
    title = " ".join(raw.split())
    company = link.company
    m = _GREENHOUSE_TITLE.match(title)
    if m:
        title, company = m.group("role").strip(), m.group("company").strip()
    title = _PLATFORM_SUFFIX.sub("", title).strip()
    return title, company or link.source.name

def is_relevant(role: str, *texts: str) -> bool:
    tokens = {t for t in re.findall(r"[a-z0-9+#.]+", role.lower()) if t not in _STOPWORDS and len(t) > 1}
    if not tokens: return True
    haystack = " ".join(texts).lower()
    return sum(t in haystack for t in tokens) / len(tokens) >= MIN_RELEVANCE

CLOSED_MARKERS = ("no longer accepting", "no longer available", "position has been filled", "job has expired", "this job is closed")

def looks_closed(*texts: str) -> bool:
    haystack = " ".join(texts).lower()
    return any(marker in haystack for marker in CLOSED_MARKERS)

def parse_roles(raw) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]", raw.strip().strip("[]"))
    roles, seen = [], set()
    for item in raw or []:
        role = " ".join(str(item).strip().strip("\"'").split())
        if role and role.lower() not in seen:
            seen.add(role.lower())
            roles.append(role)
    return roles[:MAX_ROLES_PER_USER]

@dataclass
class RunState:
    seen: set[str] = field(default_factory=set)
    stats: Counter = field(default_factory=Counter)

def serper_search(query: str, location: str) -> list[dict] | None:
    """Uses Serper.dev API to safely fetch Google results without captchas."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        log.error("Missing SERPER_API_KEY")
        return None

    url = "https://google.serper.dev/search"
    payload = json.dumps({
        "q": query,
        "location": location,
        "num": 20
    })
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=20)
        response.raise_for_status()
        results = response.json().get("organic", [])
        
        # Map Google results to our parser format
        mapped = []
        for r in results:
            mapped.append({
                "href": r.get("link"),
                "title": r.get("title"),
                "body": r.get("snippet")
            })
        return mapped
    except Exception as exc:
        log.error("Serper API error: %s", exc)
        return None

def collect_jobs(role: str, location: str, state: RunState, rows: list[dict]) -> None:
    stats = state.stats
    all_sites = [site for src in SOURCES for site in src.sites]
    chunks = [all_sites[:4], all_sites[4:]]

    for chunk in chunks:
        site_filter = " OR ".join([f"site:{site}" for site in chunk])
        query = f'"{role}" ({site_filter})'
        log.info("Searching Google (Serper): %s", query)
        
        results = serper_search(query, location)
        stats["queries"] += 1

        if results is None:
            stats["queries_failed"] += 1
            continue

        for item in results:
            link = classify(item.get("href") or "")
            if link is None:
                stats["skipped_not_posting"] += 1
                continue
            if link.url in state.seen:
                stats["skipped_duplicate"] += 1
                continue

            title, company = clean_title(item.get("title") or "", link)
            snippet = (item.get("body") or "").strip()
            if not title: continue
            if looks_closed(title, snippet):
                stats["skipped_closed"] += 1
                continue
            if not is_relevant(role, title, snippet):
                stats["skipped_irrelevant"] += 1
                continue

            state.seen.add(link.url)
            rows.append({
                "title": title,
                "company": company,
                "location": location,
                "apply_url": link.url,
                "description": snippet[:MAX_DESCRIPTION_CHARS],
            })
        time.sleep(1) # Polite delay for Serper API

def save_jobs(client: Client, rows: list[dict]) -> int:
    if not rows: return 0
    try:
        resp = client.table("jobs").upsert(rows, on_conflict="apply_url", ignore_duplicates=True).execute()
        return len(resp.data or [])
    except Exception as exc:
        log.error("Supabase write failed: %s", exc)
        return 0

def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SECRET_KEY")
    return create_client(url, key)

def main() -> int:
    client = get_client()
    users = client.table("preferences").select("target_roles, location_keyword").execute().data or []
    
    searches: dict[tuple[str, str], tuple[str, str]] = {}
    for user in users:
        location = (user.get("location_keyword") or "India").strip() or "India"
        for role in parse_roles(user.get("target_roles")):
            searches.setdefault((role.lower(), location.lower()), (role, location))

    if not searches: return 0

    state = RunState()
    for role, location in searches.values():
        log.info("=== Role: %r | Location: %r ===", role, location)
        rows: list[dict] = []
        collect_jobs(role, location, state, rows)

        inserted = save_jobs(client, rows)
        state.stats["found"] += len(rows)
        state.stats["inserted"] += inserted
        log.info("Role %r: %d valid links, %d new rows saved", role, len(rows), inserted)

    log.info("Summary: %s", dict(state.stats))
    return 0

if __name__ == "__main__":
    sys.exit(main())
