import os
import requests
from supabase import create_client

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CX = os.environ.get("GOOGLE_CX")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_SECRET:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

def search_company_careers(role, country, exp_level):
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        print("Missing GOOGLE_API_KEY or GOOGLE_CX.")
        return

    # Keep query clean and concise to ensure maximum search hits
    fresher_terms = '("fresher" OR "entry level" OR "0-2 years" OR "trainee")' if exp_level == "entry_level" else ''
    query = f'"{role}" "{country}" {fresher_terms}'.strip()
    
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX,
        "q": query,
        "gl": "in",
        "num": 10
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if "error" in data:
            print(f"Google API Error: {data['error'].get('message')}")
            return

        items = data.get("items", [])
        print(f"--> Found {len(items)} raw results for query: '{query}'")
        
        for item in items:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            display_link = item.get("displayLink", "Company Portal")

            if not title or not link:
                continue

            clean_company = (
                display_link.replace("boards.", "")
                .replace("www.", "")
                .replace(".io", "")
                .replace(".com", "")
                .replace(".in", "")
                .capitalize()
            )
            if "workatastartup" in display_link or "ycombinator" in display_link:
                clean_company = "YC Startup"
            elif "wellfound" in display_link:
                clean_company = "Wellfound Startup"

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
        print(f"Google API Exception: {e}")

def main():
    users_resp = supabase.table("preferences").select("*").execute()
    users = users_resp.data or []

    for user in users:
        roles = user.get("target_roles", [])
        country = user.get("location_keyword", "India")
        exp_level = user.get("experience_level", "entry_level")

        for role in roles:
            print(f"\nSearching: Role='{role}', Country='{country}', Exp='{exp_level}'")
            search_company_careers(role, country, exp_level)

if __name__ == "__main__":
    main()