"""Daily job search: fetch recent jobs from official career portals, filter, rank, update sheet, email top matches.

Usage:
    python main.py            # full run (needs Google + Gmail credentials)
    python main.py --dry-run  # fetch, filter and rank only; prints results
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
import matching
import outputs
import sources

ROOT = Path(__file__).parent
SENT_FILE = ROOT / "sent_jobs.json"
log = logging.getLogger("jobs")


def fetch_all(companies, cutoff):
    def one(c):
        try:
            return sources.fetch_company(c, cutoff, config.MAX_AGE_DAYS)
        except Exception as e:
            log.warning("%s (%s): %s", c["name"], c["ats"], str(e)[:120])
            return []

    with ThreadPoolExecutor(16) as ex:
        return [j for jobs in ex.map(one, companies) for j in jobs]


def filter_jobs(jobs):
    seen, candidates = set(), []
    for j in jobs:
        if j.url not in seen and matching.title_ok(j.title):
            seen.add(j.url)
            candidates.append(j)
    log.info("%d jobs with a relevant title; loading descriptions", len(candidates))

    def load(j):
        try:
            sources.load_description(j)
        except Exception as e:
            log.warning("description failed for %s: %s", j.url, str(e)[:120])

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(load, candidates))

    in_india = [j for j in candidates if matching.location_ok(j.location)]
    log.info("%d in India / remote-India", len(in_india))
    matches = [j for j in in_india if matching.experience_ok(j)]
    log.info("%d asking for at most %d year(s) minimum experience", len(matches), config.MAX_MIN_EXPERIENCE_YEARS)
    return matches


def load_sent() -> dict:
    if not SENT_FILE.exists():
        return {}
    sent = json.loads(SENT_FILE.read_text())
    keep_after = (date.today() - timedelta(days=30)).isoformat()
    return {url: d for url, d in sent.items() if d >= keep_after}


def load_dotenv():
    """For local runs: read KEY=value lines from .env without overriding real environment variables."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't touch the sheet, email or sent-jobs state")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    companies = json.loads((ROOT / "companies.json").read_text())
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_AGE_DAYS)
    jobs = fetch_all(companies, cutoff)
    log.info("%d jobs posted in the last %d days across %d companies", len(jobs), config.MAX_AGE_DAYS, len(companies))

    matches = filter_jobs(jobs)
    if matches:
        matching.Ranker((ROOT / "resume.txt").read_text()).score(matches)
    matches.sort(key=lambda j: j.score, reverse=True)

    sent = load_sent()
    top, seen_titles = [], set()
    for j in matches:  # same title posted twice by one company (e.g. per city) counts once
        key = (j.company, j.title.strip().lower())
        if j.url not in sent and key not in seen_titles and len(top) < config.TOP_N_EMAIL:
            seen_titles.add(key)
            top.append(j)

    for j in matches[:25]:
        print(f"{j.score:.3f}  {'*' if j in top else ' '} {j.company:22.22}  {j.title[:60]:60}  exp={j.required_years}  {j.location[:40]}")

    if args.dry_run:
        return

    added = outputs.update_sheet(matches)
    log.info("added %d new companies to the sheet", added)
    outputs.send_email(top, len(matches))
    log.info("emailed %d jobs to %s", len(top), config.EMAIL_TO)

    today = date.today().isoformat()
    sent.update({j.url: today for j in top})
    SENT_FILE.write_text(json.dumps(sent, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
