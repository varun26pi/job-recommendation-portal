import os
import json
import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET_KEY")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not SUPABASE_URL or not SUPABASE_SECRET:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

def analyze_job_with_groq(job, user_pref):
    if not GROQ_API_KEY:
        return True, "Matched role keywords."

    prompt = f"""
    You are a strict career hiring agent. Evaluate if this job suits the applicant.
    Applicant Target Experience: {user_pref.get('experience_level')}
    Applicant Details / Resume: {user_pref.get('resume_text', 'None provided')}
    
    Job Title: {job.get('title')}
    Job Description: {job.get('description')}

    Respond strictly in JSON format with no markdown wrappers:
    {{"eligible": true, "reason": "Short 1-sentence explanation why it fits or does not fit"}}
    """

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }

    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=10)
        data = res.json()
        content = data['choices'][0]['message']['content'].strip()
        result = json.loads(content)
        return result.get("eligible", True), result.get("reason", "Good overall match.")
    except Exception as e:
        print(f"Groq evaluation skipped due to error: {e}")
        return True, "Matched role keywords."

def send_email(to_email, matched_jobs):
    if not RESEND_API_KEY:
        print(f"Skipping email to {to_email}: RESEND_API_KEY not configured.")
        return

    html_items = ""
    for job, reason in matched_jobs[:10]:
        html_items += f"""
        <div style="margin-bottom: 20px; border-bottom: 1px solid #e5e7eb; padding-bottom: 12px;">
            <h3 style="margin: 0 0 6px 0;"><a href="{job['apply_url']}" style="color: #0070f3; text-decoration: none;">{job['title']}</a></h3>
            <p style="margin: 0 0 6px 0; color: #374151;"><strong>Company:</strong> {job['company']} | <strong>Location:</strong> {job['location']}</p>
            <p style="margin: 0; color: #6b7280; font-size: 0.9em;"><em>AI Insight: {reason}</em></p>
        </div>
        """

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Your Daily Recommended Job Digest</h2>
        <p>Here are your fresh matching jobs for today:</p>
        {html_items}
    </div>
    """

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Job Portal <onboarding@resend.dev>",
        "to": [to_email],
        "subject": f"Daily Job Digest ({len(matched_jobs)} new matches)",
        "html": html_content
    }

    res = requests.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=10)
    print(f"Sent email to {to_email}, Status Code: {res.status_code}")

def main():
    users_resp = supabase.table("preferences").select("*").execute()
    jobs_resp = supabase.table("jobs").select("*").execute()

    users = users_resp.data or []
    jobs = jobs_resp.data or []

    for user in users:
        matched_jobs = []
        user_roles = [r.lower() for r in user.get('target_roles', [])]

        for job in jobs:
            title_lower = job.get('title', '').lower()
            if any(role in title_lower for role in user_roles):
                is_eligible, reason = analyze_job_with_groq(job, user)
                if is_eligible:
                    matched_jobs.append((job, reason))

        if matched_jobs:
            send_email(user['user_email'], matched_jobs)

if __name__ == "__main__":
    main()