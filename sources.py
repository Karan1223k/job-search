"""Fetch recent job postings from companies' official careers portals.

Each fetcher takes a company entry from companies.json and returns a list of Job
objects posted within the age window. Descriptions are filled lazily by
`load_description`, so only jobs that survive the cheap filters cost an extra request.
"""
from __future__ import annotations

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (job-search-bot)", "Content-Type": "application/json"}
TIMEOUT = 30
INDIA_COUNTRY_ID = "c4f78be1a8f14da0ab49ce1162348a5e"  # Workday's global reference id for India

session = requests.Session()
session.headers.update(UA)
_adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=32)
session.mount("https://", _adapter)


@dataclass
class Job:
    company: str
    careers_url: str
    title: str
    url: str
    location: str
    posted: datetime | None
    description: str = ""
    _detail: dict = field(default_factory=dict, repr=False)  # how to fetch the description later


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def _get(url, **kw):
    r = session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def _post(url, body):
    r = session.post(url, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --- Greenhouse ---------------------------------------------------------------

def greenhouse(c, cutoff):
    t = c["token"]
    jobs = []
    for j in _get(f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs")["jobs"]:
        posted = _iso(j.get("first_published") or j.get("updated_at"))
        if posted and posted < cutoff:
            continue
        loc = (j.get("location") or {}).get("name") or ""
        jobs.append(Job(c["name"], c["careers_url"], j["title"], j["absolute_url"], loc, posted,
                        _detail={"kind": "greenhouse", "url": f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs/{j['id']}"}))
    return jobs


# --- Lever ----------------------------------------------------------------------

def lever(c, cutoff):
    jobs = []
    for j in _get(f"https://api.lever.co/v0/postings/{c['token']}?mode=json"):
        posted = datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc)
        if posted < cutoff:
            continue
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + (cats.get("allLocations") or [])
        if j.get("country") == "IN":
            locs.append("India")
        if j.get("workplaceType") == "remote":
            locs.append("Remote")
        desc = "\n".join([j.get("descriptionPlain") or ""] +
                         [l.get("text", "") + "\n" + strip_html(l.get("content", "")) for l in j.get("lists") or []] +
                         [j.get("additionalPlain") or ""])
        jobs.append(Job(c["name"], c["careers_url"], j["text"], j["hostedUrl"], " | ".join(filter(None, locs)), posted, desc))
    return jobs


# --- Ashby ----------------------------------------------------------------------

def ashby(c, cutoff):
    jobs = []
    for j in _get(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}")["jobs"]:
        if j.get("isListed") is False:
            continue
        posted = _iso(j.get("publishedAt"))
        if posted and posted < cutoff:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        locs.append(country)
        if j.get("isRemote") or j.get("workplaceType") == "Remote":
            locs.append("Remote")
        jobs.append(Job(c["name"], c["careers_url"], j["title"], j["jobUrl"], " | ".join(filter(None, locs)), posted,
                        j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))))
    return jobs


# --- SmartRecruiters ------------------------------------------------------------

def smartrecruiters(c, cutoff):
    t = c["token"]
    jobs, offset = [], 0
    while True:
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{t}/postings",
                 params={"country": "in", "limit": 100, "offset": offset})
        for j in d["content"]:
            posted = _iso(j.get("releasedDate"))
            if posted and posted < cutoff:
                continue
            loc = j.get("location") or {}
            loc_s = " | ".join(filter(None, [loc.get("city"), loc.get("region"), "India" if loc.get("country") == "in" else loc.get("country"),
                                             "Remote" if loc.get("remote") else ""]))
            jobs.append(Job(c["name"], c["careers_url"], j["name"], f"https://jobs.smartrecruiters.com/{t}/{j['id']}", loc_s, posted,
                            _detail={"kind": "smartrecruiters", "url": f"https://api.smartrecruiters.com/v1/companies/{t}/postings/{j['id']}"}))
        offset += 100
        if offset >= d.get("totalFound", 0):
            return jobs


# --- Workday --------------------------------------------------------------------

def _workday_days(posted_on: str) -> int | None:
    s = (posted_on or "").lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+? days", s)
    return int(m.group(1)) if m else None


def _find_india_facet(facets):
    for f in facets or []:
        for v in f.get("values", []):
            if v.get("id") == INDIA_COUNTRY_ID or v.get("descriptor") == "India":
                return f.get("facetParameter"), v["id"]
            if "values" in v:  # nested facet groups
                found = _find_india_facet([v])
                if found:
                    return found[0] or f.get("facetParameter"), found[1]
    return None


def workday(c, cutoff, max_days):
    host, tenant, site = c["host"], c["tenant"], c["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    first = _post(f"{api}/jobs", {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}})
    facet = _find_india_facet(first.get("facets"))
    applied = {facet[0]: [facet[1]]} if facet else {}
    search = "" if facet else "India"

    jobs, offset = [], 0
    while offset < 1000:
        d = _post(f"{api}/jobs", {"limit": 20, "offset": offset, "searchText": search, "appliedFacets": applied})
        posts = d.get("jobPostings") or []
        recent = 0
        for j in posts:
            days = _workday_days(j.get("postedOn"))
            if days is None or days > max_days:
                continue
            recent += 1
            path = j["externalPath"]
            jobs.append(Job(c["name"], c["careers_url"], j["title"], f"{c['careers_url']}{path}", j.get("locationsText", ""),
                            datetime.now(timezone.utc) - timedelta(days=days),
                            _detail={"kind": "workday", "url": f"{api}{path}"}))
        # Results are newest-first; stop once a whole page is older than the window.
        if not posts or recent == 0:
            break
        offset += 20
    return jobs


# --- SuccessFactors (RSS feed of the career site, newest first, max 20 items) ----

def successfactors(c, cutoff):
    r = session.get(f"https://{c['host']}/services/rss/job/", timeout=TIMEOUT,
                    params={"locale": "en_US", "keywords": c.get("keywords", "")})
    r.raise_for_status()
    jobs = []
    for item in ET.fromstring(r.content).iter("item"):
        pub = item.findtext("pubDate")
        if not pub:  # the "no jobs currently available" placeholder
            continue
        posted = parsedate_to_datetime(pub)
        if posted < cutoff:
            continue
        # Titles look like "Senior Data Engineer (Bangalore, IN, 562122)"
        title, _, loc = (item.findtext("title") or "").strip().rpartition(" (")
        loc = loc.rstrip(")")
        if "IN" in [p.strip() for p in loc.split(",")]:
            loc += " | India"
        url = (item.findtext("link") or "").split("?")[0]
        jobs.append(Job(c["name"], c["careers_url"], title or loc, url, loc, posted,
                        strip_html(item.findtext("description") or "")))
    return jobs


# --- Mercedes-Benz (its own job search API; country filters are ignored, so filter here) ---

MB_FIELDS = ["ID", "PositionTitle", "PositionURI", "PositionLocation.CityName",
             "PositionLocation.CountryCode", "PublicationStartDate"]


def mercedes(c, cutoff):
    jobs, first = [], 1
    while first < 5000:
        query = {"LanguageCode": "EN", "SearchCriteria": [],
                 "SearchParameters": {"FirstItem": first, "CountItem": 100, "MatchedObjectDescriptor": MB_FIELDS,
                                      "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}]}}
        items = _get("https://jobs.api.mercedes-benz.com/search", params={"data": json.dumps(query)})["SearchResult"]["SearchResultItems"]
        too_old = False
        for it in items:
            j = it["MatchedObjectDescriptor"]
            day = j.get("PublicationStartDate") or ((j.get("PublicationChannel") or [{}])[0].get("StartDate"))
            posted = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) if day else None
            if posted and posted.date() < cutoff.date():
                too_old = True
                break
            locs = j.get("PositionLocation") or []
            if not any(l.get("CountryCode") == "IN" for l in locs):
                continue
            url = j["PositionURI"].replace("jobs.mercedes-benz.com/", "jobs.mercedes-benz.com/en/", 1)
            loc = " | ".join(filter(None, [l.get("CityName") for l in locs] + ["India"]))
            jobs.append(Job(c["name"], c["careers_url"], j["PositionTitle"], url, loc, posted,
                            _detail={"kind": "page", "url": url}))
        if too_old or len(items) < 100:
            return jobs
        first += 100
    return jobs


# --- PeopleStrong (the career site's own JSON API) -----------------------------

def peoplestrong(c, cutoff):
    base = f"https://{c['host']}/api/cp/rest/altone/cp"
    jobs, offset = [], 0
    while offset < 1000:
        d = session.post(f"{base}/jobs/v1", params={"offset": offset, "limit": 45}, json={}, timeout=TIMEOUT)
        d.raise_for_status()
        d = d.json()
        posts = d.get("response") or []
        for j in posts:
            posted = datetime.fromisoformat(j["jobPostedDate"]).replace(tzinfo=timezone.utc)
            if posted.date() < cutoff.date():
                continue
            code = j["jobDetailUrl"].rstrip("/").rsplit("/", 1)[-1]
            jobs.append(Job(c["name"], c["careers_url"], j["jobTitle"], j["jobDetailUrl"],
                            (j.get("locationHierarchyComplete") or "").replace(">", " | "), posted,
                            _detail={"kind": "peoplestrong", "exp": j.get("expRange") or "",
                                     "url": f"{base}/job/{code}/v2?part=basic,descriprion,skill,qualification&isReqId=false"}))
        offset += 45
        if not posts or offset >= d.get("totalRecords", 0):
            return jobs
    return jobs


def fetch_company(c, cutoff, max_days):
    if c["ats"] == "workday":
        return workday(c, cutoff, max_days)
    return {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "successfactors": successfactors, "mercedes": mercedes, "peoplestrong": peoplestrong}[c["ats"]](c, cutoff)


def load_description(job: Job) -> None:
    """Fill job.description (and a better location for Workday) for sources that need a second request."""
    d = job._detail
    if job.description or not d:
        return
    if d["kind"] == "page":  # plain HTML job page; Mercedes returns 500s under parallel load, so retry
        for attempt in range(4):
            r = session.get(d["url"], timeout=TIMEOUT)
            if r.status_code < 500:
                break
            time.sleep(2 + 3 * attempt)
        r.raise_for_status()
        job.description = strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", r.text, flags=re.S | re.I))
        return
    data = _get(d["url"])
    if d["kind"] == "greenhouse":
        job.description = strip_html(data.get("content", ""))
        offices = [o.get("location") or o.get("name") or "" for o in data.get("offices") or []]
        job.location = " | ".join(filter(None, [job.location] + offices))
    elif d["kind"] == "peoplestrong":
        desc = strip_html((data.get("response") or {}).get("jobDescription", ""))
        job.description = (f"Required experience: {d['exp']}\n" if d["exp"] else "") + desc
    elif d["kind"] == "smartrecruiters":
        sections = (data.get("jobAd") or {}).get("sections") or {}
        job.description = "\n".join(strip_html(s.get("text", "")) for s in sections.values())
    elif d["kind"] == "workday":
        info = data.get("jobPostingInfo") or {}
        job.description = strip_html(info.get("jobDescription", ""))
        locs = [info.get("location") or ""] + (info.get("additionalLocations") or []) + [(info.get("country") or {}).get("descriptor", "")]
        job.location = " | ".join(filter(None, locs))
        if info.get("externalUrl"):
            job.url = info["externalUrl"]
