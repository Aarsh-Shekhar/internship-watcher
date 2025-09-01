# adapters.py
# -------------------------------------------------------------------
# Detect the ATS from a job URL and fetch title/location when possible.
# Falls back to parsing Open Graph meta tags from the HTML.
# -------------------------------------------------------------------

import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (JobWatcher/0.1)"}
TIMEOUT = 20


# ----------------------------- HTTP helpers ----------------------------- #
def _get_json(url, timeout=TIMEOUT):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_html(url, timeout=TIMEOUT):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def _og_meta(html):
    """Very light HTML -> {title, location, site} using OpenGraph/Twitter tags."""
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find(
                "meta", attrs={"name": n}
            )
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return ""

    title = meta("og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    desc = meta("og:description", "twitter:description") or ""
    site = meta("og:site_name") or ""

    # very light location guess from description text
    loc = ""
    mloc = re.search(
        r"(Remote|Hybrid|Onsite|[A-Za-z .'-]+,\s*[A-Z]{2}|USA|Canada|UK|Europe)",
        desc,
        re.I,
    )
    if mloc:
        loc = mloc.group(0)

    return {"title": title or "", "location": loc, "site": site}


# ----------------------------- Detector -------------------------------- #
def detect(url):
    """
    Inspect the URL and return a dict like:
      {"provider":"lever", "company":"stripe", "job":"123abc"}
    """
    u = urllib.parse.urlparse(url)
    host = u.netloc.lower().replace("www.", "")
    parts = [p for p in u.path.split("/") if p]

    # Lever: jobs.lever.co/<company>/<jobid-or-slug>
    if "lever.co" in host and len(parts) >= 2:
        return {"provider": "lever", "company": parts[0], "job": parts[1]}

    # Greenhouse: boards.greenhouse.io/<company>/jobs/<id>
    if "greenhouse.io" in host and len(parts) >= 3 and parts[1] == "jobs":
        job_id = parts[2] if parts[2].isdigit() else ""
        return {"provider": "greenhouse", "company": parts[0], "id": job_id}

    # Ashby: jobs.ashbyhq.com/<company>/<slug>
    if "ashbyhq.com" in host and len(parts) >= 2:
        return {"provider": "ashby", "company": parts[0], "slug": parts[-1]}

    # SmartRecruiters: jobs.smartrecruiters.com/<company>/<id-or-slug>
    if "smartrecruiters.com" in host and len(parts) >= 2:
        m = re.match(r"(\d+)", parts[-1])
        return {
            "provider": "smartrecruiters",
            "company": parts[0],
            "id": m.group(1) if m else "",
        }

    # Recruitee: <company>.recruitee.com/o/<slug>
    if host.endswith("recruitee.com"):
        company = host.split(".")[0]
        slug = parts[-1] if parts else ""
        return {"provider": "recruitee", "company": company, "slug": slug}

    # BambooHR: <sub>.bamboohr.com/careers/...
    if host.endswith("bamboohr.com"):
        sub = host.split(".")[0]
        return {"provider": "bamboohr", "company": sub}

    # Workday: myworkdayjobs.com / workday.com (many variants; use HTML fallback)
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return {"provider": "workday"}

    # Simplify aggregator page
    if "simplify.jobs" in host:
        return {"provider": "simplify"}

    # Oracle Cloud iRecruitment, etc. → HTML fallback
    if "oraclecloud.com" in host:
        return {"provider": "oracle"}

    # Generic fallback
    return {"provider": "generic"}


# ----------------------------- Enricher -------------------------------- #
def enrich_from_url(url):
    """
    Returns {"title": str, "location": str}
    Never raises; on failure returns empty strings.
    """
    info = detect(url)
    prov = info.get("provider", "generic")

    try:
        # ---- Lever ----
        if prov == "lever" and info.get("company") and info.get("job"):
            api = f"https://api.lever.co/v0/postings/{info['company']}/{info['job']}?mode=json"
            j = _get_json(api)
            title = j.get("text") or j.get("title") or ""
            loc = (j.get("categories") or {}).get("location") or ""
            return {"title": title, "location": loc}

        # ---- Greenhouse ----
        if prov == "greenhouse" and info.get("company") and info.get("id"):
            api = f"https://boards-api.greenhouse.io/v1/boards/{info['company']}/jobs/{info['id']}?content=true"
            j = _get_json(api)
            title = j.get("title") or ""
            loc = ""
            if isinstance(j.get("location"), dict):
                loc = j["location"].get("name") or ""
            elif isinstance(j.get("locations"), list) and j["locations"]:
                loc = ", ".join(
                    x.get("name", "") for x in j["locations"] if x.get("name")
                )
            return {"title": title, "location": loc}

        # ---- Ashby ----
        if prov == "ashby" and info.get("company"):
            board = _get_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{info['company']}"
            )
            jobs = board.get("jobs") or board.get("jobPostings") or []
            slug = (info.get("slug") or "").lower()
            for job in jobs:
                if (job.get("slug", "").lower() == slug) or (
                    slug and slug in (job.get("jobUrl", "").lower())
                ):
                    title = job.get("title") or ""
                    loc = ""
                    if isinstance(job.get("location"), dict):
                        loc = job["location"].get("name") or ""
                    elif isinstance(job.get("location"), str):
                        loc = job["location"]
                    return {"title": title, "location": loc}
            if jobs:
                return {"title": jobs[0].get("title", ""), "location": ""}

        # ---- SmartRecruiters ----
        if prov == "smartrecruiters" and info.get("company"):
            if info.get("id"):
                api = f"https://api.smartrecruiters.com/v1/companies/{info['company']}/postings/{info['id']}"
                j = _get_json(api)
                title = j.get("name") or ""
                loc = ""
                if isinstance(j.get("location"), dict):
                    pieces = [
                        j["location"].get(k, "") for k in ("city", "region", "country")
                    ]
                    loc = ", ".join([p for p in pieces if p])
                return {"title": title, "location": loc}
            # no numeric id → fall back to HTML
            html = _get_html(url)
            og = _og_meta(html)
            return {"title": og.get("title", ""), "location": og.get("location", "")}

        # ---- Recruitee ----
        if prov == "recruitee" and info.get("company"):
            board = _get_json(f"https://{info['company']}.recruitee.com/api/offers/")
            offers = board.get("offers", [])
            slug = (info.get("slug") or "").lower()
            for o in offers:
                if (o.get("slug", "").lower() == slug) or (
                    slug and slug in (o.get("careers_url", "").lower())
                ):
                    title = o.get("title") or ""
                    loc = ""
                    locs = o.get("locations") or []
                    if locs:
                        l0 = locs[0]
                        bits = [l0.get("city", ""), l0.get("country", "")]
                        loc = ", ".join([b for b in bits if b])
                    return {"title": title, "location": loc}
            if offers:
                return {"title": offers[0].get("title", ""), "location": ""}

        # ---- BambooHR ----
        if prov == "bamboohr" and info.get("company"):
            j = _get_json(f"https://{info['company']}.bamboohr.com/careers/list")
            jobs = j.get("result", {}).get("jobs", [])
            if jobs:
                title = (
                    jobs[0].get("jobOpeningName", "")
                    or jobs[0].get("jobTitle", "")
                    or ""
                )
                loc = jobs[0].get("location", "") or ""
                return {"title": title, "location": loc}

        # ---- Workday / Simplify / Oracle / Generic ----
        html = _get_html(url)
        og = _og_meta(html)
        return {"title": og.get("title", ""), "location": og.get("location", "")}

    except Exception:
        # Never let enrichment crash the pipeline
        return {"title": "", "location": ""}
