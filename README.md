# Daily Job Search

Runs every day at 9:00 AM IST on GitHub Actions:

1. Fetches jobs posted in the last 2 days directly from ~230 companies' official careers portals
   (`companies.json`: Greenhouse, Lever, Ashby, SmartRecruiters and Workday boards; no aggregators).
2. Keeps jobs in India (or remote-open-to-India) with a non-senior data science / AI / ML /
   data engineering / software engineering title and a stated
   minimum experience requirement of 0 or 1 year (ranges count by their lower bound, so "1-3 years"
   passes and "2-5 years" / "2+ years" are rejected; jobs that state no years are kept unless the
   description reads as senior).
3. Scores each job against both resumes (`resume_ai.txt`, `resume_ds.txt`; embedding similarity +
   that resume's weighted skill keywords) and ranks by the better fit.
4. Adds the name of each company with a matching job to the Google Sheet, if it isn't listed yet.
5. Emails the top 10 jobs, each labelled with the better-fitting resume; jobs emailed on an earlier
   day are tagged "(sent before)". Sent to `EMAIL_TO`.

Settings live in `config.py`; companies in `companies.json`.

## One-time setup

### 1. Google Sheet access (Apps Script web app)
1. Open the Google Sheet → Extensions → Apps Script.
2. Replace the editor contents with `apps_script.gs` and save.
3. Deploy → New deployment → type **Web app**; Execute as **Me**; Who has access **Anyone** → Deploy,
   authorize with your Google account, copy the web app URL.
   (Only requests carrying the `TOKEN` in the script can add rows.)

### 2. Gmail app password
1. Turn on 2-Step Verification on the Gmail account that sends the mail.
2. https://myaccount.google.com/apppasswords → create one → copy the 16-character password.

### 3. GitHub repo + secrets
1. Create a **private** GitHub repo (the resume is in it) and push this folder.
2. Repo → Settings → Secrets and variables → Actions → New repository secret:
   - `SHEET_WEBAPP_URL`: the Apps Script web app URL
   - `SHEET_TOKEN`: a random string; paste the same value as `TOKEN` in the Apps Script
   - `GMAIL_ADDRESS`: the sending Gmail address
   - `GMAIL_APP_PASSWORD`: the app password
3. Actions tab → "Daily job search" → **Run workflow** to test it once.

## Running locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --dry-run          # prints ranked matches, touches nothing
```
For a full local run, put `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SHEET_WEBAPP_URL` and
`SHEET_TOKEN` in a `.env` file (git-ignored).

## Notes
- GitHub's scheduler is best-effort: runs can start late or be skipped at busy times. The
  workflow therefore has backup slots (09:22, 10:07, 11:43 IST); `last_run.txt` records the
  day's run so only the first successful one sends the email. Manual runs always run.
- Scheduled workflows are paused by GitHub after 60 days without repo activity; the daily
  commit of `last_run.txt` keeps the repo active.
- To add a company, add an entry to `companies.json` following the existing ones.
