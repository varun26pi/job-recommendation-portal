import os
import time
from ddgs import DDGS
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_SECRET:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

def search_ddg_jobs(role, country, exp_level):
    # Cleaner query targeting real job posting titles
    query = f'"{role}" job {country} site:naukri.com OR site:greenhouse.io OR site:lever.co OR site:wellfound.com'
    
    print(f"--> Searching DuckDuckGo for query: '{query}'")

    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=10))
        print(f"    Found {len(results)} raw results.")

        for item in results:
            title = item.get("title", "")
            link = item.get("href", "")
            snippet = item.get("body", "")

            if not title or not link:
                continue

            # Filter out non-job URLs (Wikipedia, definitions, generic pages)
            valid_domains = ["naukri.com", "greenhouse.io", "lever.co", "wellfound.com", "workatastartup.com", "workday.com"]
            if not any(domain in link for domain in valid_domains):
                continue

            clean_company = "Career Portal"
            if "greenhouse.io" in link: clean_company = "Greenhouse"
            elif "lever.co" in link: clean_company = "Lever"
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

        for role in roles[:3]:
            print(f"\nSearching: Role='{role}', Country='{country}', Exp='{exp_level}'")
            search_ddg_jobs(role, country, exp_level)
            time.sleep(1)

if __name__ == "__main__":
    main()