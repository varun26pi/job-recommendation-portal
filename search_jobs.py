import os
import time
from duckduckgo_search import DDGS
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_SECRET:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

def search_ddg_jobs(role, country, exp_level):
    fresher_terms = '("fresher" OR "entry level" OR "0-2 years" OR "trainee")' if exp_level == "entry_level" else ''
    query = f'"{role}" "{country}" {fresher_terms} (site:greenhouse.io OR site:lever.co OR site:workatastartup.com OR site:wellfound.com OR site:naukri.com)'
    
    print(f"--> Searching DuckDuckGo for query: '{query}'")

    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=10))
        print(f"    Found {len(results)} results.")

        for item in results:
            title = item.get("title", "")
            link = item.get("href", "")
            snippet = item.get("body", "")

            if not title or not link:
                continue

            clean_company = "Career Portal"
            if "greenhouse.io" in link: clean_company = "Greenhouse"
            elif "lever.co" in link: clean_company = "Lever"
            elif "workatastartup.com" in link: clean_company = "YC Startup"
            elif "wellfound.com" in link: clean_company = "Wellfound"
            elif "naukri.com" in link: clean_company = "Naukri"

            job_payload = {
                "title": title,
                "company": clean_company,
                "location": country,
                "apply_url": link,
                "description": snippet
            }

            try:
                supabase.table("jobs").upsert(job_payload, on_conflict="apply_url").execute()
                print(f"    [SAVED] {title} ({clean_company})")
            except Exception as e:
                print(f"    [SUPABASE ERROR] {e}")

    except Exception as e:
        print(f"    [DDG EXCEPTION] {e}")

def main():
    users_resp = supabase.table("preferences").select("*").execute()
    users = users_resp.data or []

    for user in users:
        roles = user.get("target_roles", [])
        country = user.get("location_keyword", "India")
        exp_level = user.get("experience_level", "entry_level")

        for role in roles[:3]:  # Top 3 roles per user
            print(f"\nSearching: Role='{role}', Country='{country}', Exp='{exp_level}'")
            search_ddg_jobs(role, country, exp_level)
            time.sleep(1)  # Polite delay to prevent rate limits

if __name__ == "__main__":
    main()