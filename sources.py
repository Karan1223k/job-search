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
from pathlib import Path

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


def _send(method, url, **kw):
    """Make a request, waiting and retrying if the site says we're going too fast."""
    for attempt in range(6):
        r = session.request(method, url, timeout=TIMEOUT, **kw)
        if r.status_code != 429 or attempt == 5:
            break
        wait = r.headers.get("Retry-After", "")
        time.sleep(min(int(wait), 30) if wait.isdigit() else 3 + 4 * attempt)
    r.raise_for_status()
    return r.json()


def _get(url, **kw):
    return _send("GET", url, **kw)


def _post(url, body):
    return _send("POST", url, json=body)


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
        try:
            d = _post(f"{api}/jobs", {"limit": 20, "offset": offset, "searchText": search, "appliedFacets": applied})
        except requests.RequestException:
            if not offset:
                raise
            break  # keep the pages we already have
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
        # Facet results are newest-first, so stop once a whole page is older than the window.
        # Text-search results are ordered by relevance, so those have to be read to the end.
        if not posts or (facet and recent == 0):
            break
        offset += 20
    return jobs


# --- SuccessFactors (RSS feed of the career site, newest first, max 20 items) ----

def successfactors(c, cutoff):
    jobs, seen = [], set()
    # "keywords" can hold several searches split by "|", since each feed stops at 20 items
    for keywords in c.get("keywords", "").split("|"):
        r = session.get(f"https://{c['host']}/services/rss/job/", timeout=TIMEOUT,
                        params={"locale": "en_US", "keywords": keywords})
        r.raise_for_status()
        for item in ET.fromstring(r.content).iter("item"):
            pub = item.findtext("pubDate")
            if not pub:  # the "no jobs currently available" placeholder
                continue
            posted = parsedate_to_datetime(pub)
            url = (item.findtext("link") or "").split("?")[0]
            if posted < cutoff or url in seen:
                continue
            seen.add(url)
            # Titles look like "Senior Data Engineer (Bangalore, IN, 562122)"
            title, _, loc = (item.findtext("title") or "").strip().rpartition(" (")
            loc = loc.rstrip(")")
            if "IN" in [p.strip() for p in loc.split(",")]:
                loc += " | India"
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


# --- Oracle Cloud HCM (newest first; location ids differ per company, so filter India here) ---

def oracle(c, cutoff):
    host, site = c["host"], c["site"]
    api = f"https://{host}/hcmRestApi/resources/latest"
    jobs, offset = [], 0
    while offset < 2000:
        finder = f"findReqs;siteNumber={site},limit=25,offset={offset},sortBy=POSTING_DATES_DESC"
        items = _get(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}")["items"]
        posts = (items[0].get("requisitionList") or []) if items else []
        for j in posts:
            posted = datetime.fromisoformat(j["PostedDate"]).replace(tzinfo=timezone.utc)
            if posted.date() < cutoff.date():
                return jobs
            others = j.get("secondaryLocations") or []
            if j.get("PrimaryLocationCountry") != "IN" and not any(o.get("CountryCode") == "IN" for o in others):
                continue
            loc = " | ".join(filter(None, [j.get("PrimaryLocation")] + [o.get("Name") for o in others] + ["India"]))
            jobs.append(Job(c["name"], c["careers_url"], j["Title"],
                            f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}", loc, posted,
                            _detail={"kind": "oracle", "url": f"{api}/recruitingCEJobRequisitionDetails?onlyData=true&expand=all"
                                                              f"&finder=ById;Id=%22{j['Id']}%22,siteNumber={site}"}))
        if len(posts) < 25:
            return jobs
        offset += 25
    return jobs


# --- Eightfold (the career site's own search API) --------------------------------

def eightfold(c, cutoff):
    host, domain = c["host"], c["domain"]
    jobs, start = [], 0
    while start < 1000:
        posts = _get(f"https://{host}/api/pcsx/search",
                     params={"domain": domain, "query": "", "location": "India", "start": start, "sort_by": "timestamp"})["data"]["positions"]
        recent = 0
        for j in posts:
            posted = datetime.fromtimestamp(j["postedTs"], tz=timezone.utc)
            if posted < cutoff:
                continue
            recent += 1
            jobs.append(Job(c["name"], c["careers_url"], j["name"], f"https://{host}{j['positionUrl']}",
                            " | ".join(j.get("locations") or []), posted,
                            _detail={"kind": "eightfold", "url": f"https://{host}/api/pcsx/position_details?position_id={j['id']}&domain={domain}&hl=en"}))
        if not posts or recent == 0:
            return jobs
        start += len(posts)
    return jobs


# --- AMD (its own job search API, descriptions included) --------------------------

def amd(c, cutoff):
    jobs, page = [], 1
    while page <= 20:
        d = _get("https://careers.amd.com/api/jobs",
                 params={"location": "India", "page": page, "limit": 100, "sortBy": "posted_date", "descending": "true"})
        posts = d.get("jobs") or []
        for x in posts:
            j = x["data"]
            posted = datetime.strptime(j["posted_date"], "%Y-%m-%dT%H:%M:%S%z")
            if posted < cutoff:
                return jobs
            if j.get("country_code") != "IN":
                continue
            desc = "\n".join(strip_html(j.get(k) or "") for k in ("description", "qualifications", "responsibilities"))
            jobs.append(Job(c["name"], c["careers_url"], j["title"], f"https://careers.amd.com/careers-home/jobs/{j['slug']}",
                            f"{j.get('full_location') or j.get('city') or ''} | India", posted, desc))
        if len(posts) < 100:
            return jobs
        page += 1
    return jobs


# --- Amazon (amazon.jobs search API, descriptions included; "recent" order is only roughly by date) ---

def amazon(c, cutoff):
    jobs, offset = [], 0
    while offset < 3000:
        posts = _get("https://www.amazon.jobs/en/search.json",
                     params={"country": "IND", "sort": "recent", "result_limit": 100, "offset": offset}).get("jobs") or []
        recent = 0
        for j in posts:
            posted = datetime.strptime(j["posted_date"], "%B %d, %Y").replace(tzinfo=timezone.utc)
            if posted.date() < cutoff.date():
                continue
            recent += 1
            if j.get("is_intern"):
                continue
            desc = "\n".join(strip_html(j.get(k) or "") for k in ("description", "basic_qualifications", "preferred_qualifications"))
            jobs.append(Job(c["name"], c["careers_url"], j["title"], f"https://www.amazon.jobs{j['job_path']}",
                            f"{j.get('normalized_location') or j.get('location') or ''} | India", posted, desc))
        if len(posts) < 100 or recent == 0:
            return jobs
        offset += 100
    return jobs


# --- IBM (its own search API; it gives no posting date, so a job counts as posted on the day we first see it) ---

IBM_SEEN_FILE = Path(__file__).parent / "ibm_seen.json"
_ibm_seen = None


def ibm(c, cutoff):
    global _ibm_seen
    found, offset = {}, 0
    while offset < 5000:
        body = {"appId": "careers", "scopes": ["careers2"], "query": {"bool": {"must": []}},
                "post_filter": {"term": {"field_keyword_05": "India"}}, "size": 100, "from": offset, "sort": [{"_id": "asc"}],
                "lang": "zz", "localeSelector": {}, "sm": {"query": "", "lang": "zz"},
                "_source": ["title", "url", "body", "field_keyword_19"]}
        hits = _post("https://www-api.ibm.com/search/api/v2", body)["hits"]
        found.update((h["_source"]["url"], h["_source"]) for h in hits["hits"])
        offset += 100
        if not hits["hits"] or offset >= hits["total"]["value"]:
            break

    known = json.loads(IBM_SEEN_FILE.read_text()) if IBM_SEEN_FILE.exists() else None
    today = datetime.now(timezone.utc).date().isoformat()
    seen, jobs = dict(known or {}), []
    for url, j in found.items():
        # With no history yet, everything listed today is treated as old, so only later arrivals count as new.
        first = seen.setdefault(url, today if known is not None else "2000-01-01")
        posted = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
        if posted.date() < cutoff.date():
            continue
        jobs.append(Job(c["name"], c["careers_url"], j["title"], url, f"{j.get('field_keyword_19') or ''} | India", posted,
                        strip_html(j.get("body") or "")))
    _ibm_seen = seen
    return jobs


def save_ibm_seen() -> None:
    if _ibm_seen is not None:
        IBM_SEEN_FILE.write_text(json.dumps(_ibm_seen, indent=0, sort_keys=True))


def fetch_company(c, cutoff, max_days):
    if c["ats"] == "workday":
        return workday(c, cutoff, max_days)
    return {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "successfactors": successfactors, "mercedes": mercedes, "peoplestrong": peoplestrong,
            "oracle": oracle, "eightfold": eightfold, "amd": amd, "amazon": amazon, "ibm": ibm}[c["ats"]](c, cutoff)


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
    elif d["kind"] == "oracle":
        info = (data.get("items") or [{}])[0]
        job.description = "\n".join(strip_html(info.get(k) or "") for k in
                                    ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
    elif d["kind"] == "eightfold":
        job.description = strip_html((data.get("data") or {}).get("jobDescription", ""))
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
