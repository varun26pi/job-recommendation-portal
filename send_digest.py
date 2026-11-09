import os
import json
import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET_KEY")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not SUPABASE_URL or not SUPABASE_SECRET:
    raise ValueError("Missing Supabase credentials.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

def analyze_job_with_groq(job, user_pref):
    if not GROQ_API_KEY:
        return True, "Matched user preferences."

    prompt = f"""
    You are a career matching agent. Evaluate if this job is suitable for the candidate.
    Candidate Target Roles: {user_pref.get('target_roles')}
    Candidate Experience Level: {user_pref.get('experience_level')}
    Candidate Resume Summary: {user_pref.get('resume_text', 'Entry level applicant')}

    Job Title: {job.get('title')}
    Job Description: {job.get('description')}

    Respond ONLY with valid JSON in this format:
    {{"eligible": true, "reason": "1-sentence reason why it fits"}}
    """

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=12)
        data = res.json()
        content = data['choices'][0]['message']['content'].strip()
        result = json.loads(content)
        return result.get("eligible", True), result.get("reason", "Good overall match.")
    except Exception as e:
        print(f"    [GROQ WARN] Falling back to default match: {e}")
        return True, "Matched role keywords."

def send_email_digest(to_email, matched_jobs):
    if not RESEND_API_KEY:
        print("    [RESEND ERROR] Missing RESEND_API_KEY environment variable.")
        return

    job_rows = ""
    for item in matched_jobs:
        job = item['job']
        reason = item['reason']
        job_rows += f"""
        <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #e0e0e0; border-radius: 8px;">
            <h3 style="margin: 0 0 5px 0; color: #1a73e8;"><a href="{job.get('apply_url')}" target="_blank">{job.get('title')}</a></h3>
            <p style="margin: 0 0 5px 0; color: #555;"><strong>Company:</strong> {job.get('company')} | <strong>Location:</strong> {job.get('location')}</p>
            <p style="margin: 0; color: #333;"><strong>AI Insight:</strong> {reason}</p>
        </div>
        """

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>🎯 Daily Job Recommendations</h2>
        <p>Here are your personalized job matches for today:</p>
        {job_rows}
        <hr style="border: none; border-top: 1px solid #ccc; margin-top: 30px;">
        <p style="font-size: 12px; color: #777;">Automated daily dispatch from your Job Recommendation Portal.</p>
      </body>
    </html>
    """

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Job Portal <onboarding@resend.dev>",
        "to": [to_email],
        "subject": "🎯 Your Daily AI Job Digest",
        "html": html_content
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code in [200, 201]:
            print(f"    [EMAIL SUCCESS] Sent digest to {to_email} (Status {response.status_code})")
        else:
            print(f"    [RESEND API ERROR] Status {response.status_code}: {response.text}")
    except Exception as e:
        print(f"    [EMAIL EXCEPTION] Failed to send email: {e}")

def main():
    users_resp = supabase.table("preferences").select("*").execute()
    users = users_resp.data or []

    jobs_resp = supabase.table("jobs").select("*").order("created_at", desc=True).limit(20).execute()
    jobs = jobs_resp.data or []

    if not jobs:
        print("No jobs found in database to evaluate.")
        return

    for user in users:
        recipient_email = user.get("email")
        if not recipient_email:
            continue

        print(f"\nEvaluating jobs for user: {recipient_email}")
        matched_jobs = []

        for job in jobs:
            is_eligible, reason = analyze_job_with_groq(job, user)
            if is_eligible:
                matched_jobs.append({"job": job, "reason": reason})

        print(f"Matched {len(matched_jobs)} / {len(jobs)} jobs for {recipient_email}")

        if matched_jobs:
            send_email_digest(recipient_email, matched_jobs)
        else:
            print(f"No matching jobs for {recipient_email} after AI evaluation.")

if __name__ == "__main__":
    main()