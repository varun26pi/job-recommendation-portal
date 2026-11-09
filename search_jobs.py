#!/usr/bin/env python3
"""
Job aggregator: finds direct job-posting links on public ATS platforms via
DuckDuckGo and upserts them into a Supabase `jobs` table.

Environment variables
    SUPABASE_URL, SUPABASE_SECRET_KEY   required
    LOG_LEVEL                           optional, default INFO
    SEARCH_TIMELIMIT                    optional: d | w | m | y | none (default m)
    MAX_ROLES_PER_USER                  optional, default 3
    REQUEST_DELAY                       optional, seconds between searches (default 2.0)

Assumes a UNIQUE constraint on jobs.apply_url (needed for on_conflict).
"""
from __future__ import annotations

import logging
import os
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

from ddgs import DDGS
from supabase import Client, create_client

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
MAX_ROLES_PER_USER = int(os.getenv("MAX_ROLES_PER_USER", "3"))
RESULTS_PER_QUERY = 15
MAX_RETRIES = 3
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))
MAX_DESCRIPTION_CHARS = 1000
MIN_RELEVANCE = 0.5  # share of role keywords that must appear in title/snippet

_tl = os.getenv("SEARCH_TIMELIMIT", "m").strip().lower()
SEARCH_TIMELIMIT = None if _tl in ("", "none") else _tl

# DuckDuckGo region codes. Unknown locations fall back to worldwide.
REGIONS = {
    "india": "in-en",
    "united states": "us-en",
    "usa": "us-en",
    "us": "us-en",
    "united kingdom": "uk-en",
    "uk": "uk-en",
    "canada": "ca-en",
    "australia": "au-en",
    "germany": "de-de",
    "singapore": "sg-en",
}

CLOSED_MARKERS = (
    "no longer accepting",
    "no longer available",
    "position has been filled",
    "job has expired",
    "this job is closed",
    "posting has expired",
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("job_scraper")

# --------------------------------------------------------------------------- #
# Supported platforms
# --------------------------------------------------------------------------- #
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


@dataclass(frozen=True)
class Source:
    name: str
    sites: tuple[str, ...]          # what we pass to `site:` in the search query
    hosts: tuple[str, ...]          # hostnames (and their subdomains) we accept
    path_re: re.Pattern[str]        # the FULL path of a real single-posting page
    company_from_subdomain: bool = False


SOURCES: tuple[Source, ...] = (
    Source(
        "Greenhouse",
        ("boards.greenhouse.io", "job-boards.greenhouse.io"),
        ("boards.greenhouse.io", "job-boards.greenhouse.io"),
        re.compile(r"^/(?P<company>[\w-]+)/jobs/\d+$"),
    ),
    Source(
        "Lever",
        ("jobs.lever.co",),
        ("jobs.lever.co", "jobs.eu.lever.co"),
        re.compile(rf"^/(?P<company>[\w.-]+)/{UUID}$", re.I),
    ),
    Source(
        "Ashby",
        ("jobs.ashbyhq.com",),
        ("jobs.ashbyhq.com",),
        re.compile(rf"^/(?P<company>[\w.%-]+)/{UUID}$", re.I),
    ),
    Source(
        "Workable",
        ("apply.workable.com",),
        ("apply.workable.com",),
        re.compile(r"^/(?P<company>[\w-]+)/j/[A-Za-z0-9]+$"),
    ),
    Source(
        "Naukri",
        ("naukri.com",),
        ("naukri.com",),
        re.compile(r"^/job-listings-[\w-]+$"),
    ),
    Source(
        "Workday",
        ("myworkdayjobs.com",),
        ("myworkdayjobs.com",),
        re.compile(r"^(?:/[a-z]{2}-[A-Za-z]{2})?/[\w-]+/job/.+$"),
        company_from_subdomain=True,
    ),
)


@dataclass(frozen=True)
class JobLink:
    source: Source
    company: str | None
    url: str  # canonical URL: https, no query/fragment, no trailing slash


def _prettify(slug: str | None) -> str | None:
    if not slug:
        return None
    return unquote(slug).replace("-", " ").replace("_", " ").strip().title() or None


def classify(url: str) -> JobLink | None:
    """Return a JobLink if `url` is a single job posting on a supported ATS."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None

    host = parts.hostname.lower()
    path = re.sub(r"/apply$", "", parts.path.rstrip("/"))

    for src in SOURCES:
        if not any(host == h or host.endswith("." + h) for h in src.hosts):
            continue
        match = src.path_re.match(path)
        if not match:
            return None  # right platform, but a board/landing page, not a posting
        slug = host.split(".")[0] if src.company_from_subdomain else match.groupdict().get("company")
        return JobLink(src, _prettify(slug), urlunsplit(("https", host, path, "", "")))
    return None


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
_GREENHOUSE_TITLE = re.compile(r"^job application for\s+(?P<role>.+?)\s+at\s+(?P<company>.+)$", re.I)
_PLATFORM_SUFFIX = re.compile(r"\s*[|\-–]\s*(naukri\.com|lever|greenhouse|workable|ashby)\s*$", re.I)
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
    if not tokens:
        return True
    haystack = " ".join(texts).lower()
    return sum(t in haystack for t in tokens) / len(tokens) >= MIN_RELEVANCE


def looks_closed(*texts: str) -> bool:
    haystack = " ".join(texts).lower()
    return any(marker in haystack for marker in CLOSED_MARKERS)


def parse_roles(raw) -> list[str]:
    """Accept a list, a comma/newline-separated string, or None; dedupe, cap."""
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]", raw)
    roles, seen = [], set()
    for item in raw or []:
        role = " ".join(str(item).split())
        if role and role.lower() not in seen:
            seen.add(role.lower())
            roles.append(role)
    return roles[:MAX_ROLES_PER_USER]


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def polite_sleep() -> None:
    time.sleep(REQUEST_DELAY + random.uniform(0, 1))


def ddg_search(query: str, region: str) -> list[dict] | None:
    """Run one DDG query with exponential backoff. Returns None if it kept failing."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with DDGS() as ddgs:
                return list(
                    ddgs.text(
                        query,
                        region=region,
                        timelimit=SEARCH_TIMELIMIT,
                        max_results=RESULTS_PER_QUERY,
                    )
                )
        except Exception as exc:  # ddgs raises several distinct exception types
            if "no results" in str(exc).lower():
                return []
            if attempt == MAX_RETRIES:
                log.error("Search failed after %d attempts: %s (%s)", attempt, query, exc)
                return None
            delay = REQUEST_DELAY * 2 ** attempt + random.uniform(0, 1)
            log.warning("Search error (attempt %d/%d): %s. Retrying in %.1fs", attempt, MAX_RETRIES, exc, delay)
            time.sleep(delay)
    return None


def collect_jobs(role: str, location: str, seen: set[str], stats: Counter) -> list[dict]:
    region = REGIONS.get(location.lower(), "wt-wt")
    rows: list[dict] = []

    for src in SOURCES:
        for site in src.sites:
            query = f'"{role}" {location} site:{site}'
            log.info("Searching: %s", query)
            results = ddg_search(query, region)
            stats["queries"] += 1
            if results is None:
                stats["queries_failed"] += 1
                continue

            for item in results:
                link = classify(item.get("href") or "")
                if link is None:
                    stats["skipped_not_posting"] += 1
                    continue
                if link.url in seen:
                    stats["skipped_duplicate"] += 1
                    continue

                title, company = clean_title(item.get("title") or "", link)
                snippet = (item.get("body") or "").strip()
                if not title:
                    continue
                if looks_closed(title, snippet):
                    stats["skipped_closed"] += 1
                    continue
                if not is_relevant(role, title, snippet):
                    stats["skipped_irrelevant"] += 1
                    continue

                seen.add(link.url)
                rows.append(
                    {
                        "title": title,
                        "company": company,
                        "location": location,
                        "apply_url": link.url,
                        "description": snippet[:MAX_DESCRIPTION_CHARS],
                    }
                )
            polite_sleep()
    return rows


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def save_jobs(client: Client, rows: list[dict]) -> int:
    """Batch insert; existing apply_urls are left untouched. Returns new-row count."""
    if not rows:
        return 0
    try:
        resp = (
            client.table("jobs")
            .upsert(rows, on_conflict="apply_url", ignore_duplicates=True)
            .execute()
        )
        return len(resp.data or [])
    except Exception as exc:
        log.error("Supabase write failed for %d rows: %s", len(rows), exc)
        return 0


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SECRET_KEY environment variables.")
    return create_client(url, key)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    client = get_client()
    users = client.table("preferences").select("target_roles, location_keyword").execute().data or []
    log.info("Loaded %d user preference rows", len(users))

    # Many users share the same (role, location): search each pair only once.
    searches: dict[tuple[str, str], tuple[str, str]] = {}
    for user in users:
        location = (user.get("location_keyword") or "India").strip() or "India"
        for role in parse_roles(user.get("target_roles")):
            searches.setdefault((role.lower(), location.lower()), (role, location))

    if not searches:
        log.warning("No roles to search for.")
        return 0

    stats: Counter = Counter()
    seen: set[str] = set()

    for role, location in searches.values():
        log.info("=== Role: %r | Location: %r ===", role, location)
        try:
            rows = collect_jobs(role, location, seen, stats)
            inserted = save_jobs(client, rows)
            stats["found"] += len(rows)
            stats["inserted"] += inserted
            log.info("Role %r: %d valid, %d new", role, len(rows), inserted)
        except Exception:
            log.exception("Unexpected failure while processing role %r", role)
            stats["role_errors"] += 1

    log.info("Summary: %s", dict(stats))

    # Non-zero exit so cron / CI notices when search is wholly broken.
    if stats["queries"] and stats["queries_failed"] == stats["queries"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())