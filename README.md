# Automated AI Job Recommendation Portal

An end-to-end, zero-cost daily job recommendation engine built for candidates, freshers, and tech professionals. It collects user career preferences once via a web form, automatically searches company career portals and startup boards daily, evaluates fit using **Groq AI (Llama 3.1)**, and delivers personalized job digests straight to your inbox.

**Live Preferences Portal:** [https://varun26pi.github.io/job-recommendation-portal/](https://varun26pi.github.io/job-recommendation-portal/)

---

##  Features

- **Single Form Setup:** Users input target roles, location (e.g., India, Remote), educational qualification (e.g., B.Tech, MCA), experience level, and resume text once.
- **Multi-User Ready:** Anyone can submit their details on the web portal to receive their own daily job emails.
- **Multi-Board Aggregation:** Programmatically searches across major ATS platforms (**Greenhouse, Lever, Workday, Ashby, Jobvite, Breezy, Workable**), startup boards (**YC Work at a Startup, Wellfound**), and job platforms (**Naukri, Foundit, Unstop**).
- **Fresher & Off-Campus Optimized:** Tailored specifically to filter entry-level roles (0–2 YOE), off-campus drives, and trainee positions.
- **AI Matching Engine:** Powered by **Groq AI (Llama-3.1-8b)** to compare scraped job titles and descriptions against the applicant's resume and target experience level, providing a 1-sentence match insight for every job.
- **100% Free & Serverless:** Completely hosted and automated using **GitHub Pages**, **Supabase**, **GitHub Actions**, and **Resend**. Runs daily at 08:00 AM UTC without requiring a local machine or paid server.

---

## Architecture & Workflow

```text
[ User / Friends ]
       │ (Inputs preferences)
       ▼
[ GitHub Pages Frontend ] ──▶ Saves to ──▶ [ Supabase Database ]
                                                 ▲
                                                 │ Reads preferences & stores jobs
                                                 ▼
                             [ GitHub Actions (Daily Cron @ 8 AM UTC) ]
                                 ├── 1. Run `search_jobs.py` (Google Custom Search API)
                                 ├── 2. Run `send_digest.py` (Groq AI Matching)
                                 └── 3. Send HTML Email Digest via Resend API
                                                 │
                                                 ▼
                                       [ User's Email Inbox ]
