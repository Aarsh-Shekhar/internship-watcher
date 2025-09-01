# watch_core.py
import os
import re
import json
import sqlite3
import hashlib
import subprocess
import datetime as dt
from typing import List, Dict, Tuple

import requests

# ---------- config ----------
BRANCHES = ["dev", "main", "master"]
ATS_DOMAINS = [
    "simplify.jobs",
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "icims.com",
    "bamboohr.com",
    "workable.com",
    "jobvite.com",
    "oraclecloud.com",
    "recruitee.com",
    "adp.com",
    "dayforcehcm.com",
]
FEED_PATH = "feed.json"  # rolling feed used by the UI


# ---------- light helpers ----------
def _now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect("seen.db")
    c = conn.cursor()
    c.execute(
        """
      CREATE TABLE IF NOT EXISTS seen(
        id TEXT PRIMARY KEY,
        company TEXT,
        url TEXT UNIQUE,
        first_seen TEXT
      )
    """
    )
    try:
        c.execute("ALTER TABLE seen ADD COLUMN first_seen TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _fetch_readme(repo: str) -> str:
    for br in BRANCHES:
        url = f"https://raw.githubusercontent.com/{repo}/{br}/README.md"
        r = requests.get(
            url,
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=25,
        )
        if r.status_code == 404:
            continue
        r.raise_for_status()
        return r.text
    return ""


LINK_RX_1 = re.compile(r"\((https?://[^)]+)\)")
LINK_RX_2 = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)


def _extract_links(md: str) -> List[str]:
    out = set(LINK_RX_1.findall(md)) | set(LINK_RX_2.findall(md))
    return [u.strip() for u in out]


def _is_app_url(url: str) -> bool:
    u = url.lower()
    if "top-list" in u:
        return False
    return any(d in u for d in ATS_DOMAINS)


# ---- optional enrichment via adapters.py ----
def _details_for(url: str) -> Dict[str, str]:
    """
    Best-effort details for {title, location}. Uses adapters.py if present,
    otherwise falls back to simple OpenGraph scraping.
    """
    try:
        from adapters import details_for as adapters_details_for  # type: ignore

        d = adapters_details_for(url) or {}
        return {
            "title": d.get("title") or d.get("og:title") or "",
            "location": d.get("location") or "",
            "site": d.get("site") or "",
        }
    except Exception:
        try:
            html = requests.get(url, timeout=15).text
        except Exception:
            return {"title": "", "location": "", "site": ""}
        title, loc, site = "", "", ""
        for mname in ("og:title", "twitter:title"):
            m = re.search(
                rf'<meta[^>]+property=["\']{mname}["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                re.I,
            )
            if m:
                title = m.group(1).strip()
                break
        m = re.search(
            r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        if m:
            site = m.group(1).strip()
        m = re.search(
            r"(Remote|Hybrid|Onsite|USA|Canada|Europe|[A-Z][a-zA-Z]+,\s*[A-Z]{2})", html
        )
        if m:
            loc = m.group(1)
        return {"title": title, "location": loc, "site": site}


# ---------- filters ----------
def _load_filters() -> Dict:
    default = {
        "include_keywords": [],
        "exclude_keywords": [],
        "company_allowlist": [],
        "company_blocklist": [],
        "locations_any": [],
        "priority_companies": [],
        "priority_keywords": [],
    }
    return _load_json("filters.json", default)


def _passes_filters(company: str, det: Dict[str, str], F: Dict) -> bool:
    text = " ".join(
        [company or "", det.get("title", ""), det.get("location", "")]
    ).lower()

    if F["company_blocklist"] and company.lower() in [
        c.lower() for c in F["company_blocklist"]
    ]:
        return False
    if F["company_allowlist"]:
        if company.lower() not in [c.lower() for c in F["company_allowlist"]]:
            return False
    if F["exclude_keywords"] and any(k.lower() in text for k in F["exclude_keywords"]):
        return False
    if F["include_keywords"] and not any(
        k.lower() in text for k in F["include_keywords"]
    ):
        return False
    if F["locations_any"]:
        if not any(
            k.lower() in det.get("location", "").lower() for k in F["locations_any"]
        ):
            return False
    return True


def _is_priority(company: str, det: Dict[str, str], F: Dict) -> bool:
    t = " ".join([company or "", det.get("title", "")]).lower()
    return (
        F["priority_companies"]
        and company.lower() in [c.lower() for c in F["priority_companies"]]
    ) or (
        F["priority_keywords"] and any(k.lower() in t for k in F["priority_keywords"])
    )


# ---------- notifiers ----------
def notify_mac(title: str, body: str):
    """Robust macOS banner via osascript (absolute path + safe quoting)."""
    osa = "/usr/bin/osascript" if os.path.exists("/usr/bin/osascript") else "osascript"
    try:
        cmd = [
            osa,
            "-e",
            f"display notification {json.dumps(body)} with title {json.dumps(title)}",
        ]
        r = subprocess.run(cmd, capture_output=True)
        print(
            f"[mac] rc={r.returncode} out={r.stdout.decode().strip()} err={r.stderr.decode().strip()}"
        )
    except Exception as e:
        print(f"[mac] error: {e}")


def phone_notify(title: str, body: str, priority: int = 3):
    topic = os.getenv("NTFY_TOPIC", "aarsh-internships")
    try:
        r = requests.post(
            f"https://ntfy.sh/{topic}",
            headers={"Title": title, "Priority": str(priority)},
            data=body.encode("utf-8"),
            timeout=8,
        )
        print(f"[ntfy] topic={topic} status={r.status_code}")
    except Exception as e:
        print(f"[ntfy] error: {e}")


# ---------- FEED ----------
def _append_feed(items: List[Dict]):
    feed = _load_json(FEED_PATH, [])
    feed.extend(items)
    if len(feed) > 500:
        feed = feed[-500:]
    _save_json(FEED_PATH, feed)


def load_feed() -> List[Dict]:
    f = _load_json(FEED_PATH, [])
    try:
        f.sort(key=lambda x: x.get("ts", ""), reverse=True)
    except Exception:
        pass
    return f


# ---------- main scan ----------
def run_scan(
    seed: bool = False, notify_when_zero: bool = True, zero_prefix: str = ""
) -> Tuple[List[Dict], int]:
    """
    Returns (new_items_list, kept_count_after_filters).
    Each item: {ts,label,company,title,location,url,urgent}
    """
    sources = _load_json(
        "sources.json",
        [
            {
                "label": "Simplify 2026 Internships",
                "repo": "SimplifyJobs/Summer2026-Internships",
            }
        ],
    )
    F = _load_filters()

    # collect candidates
    candidates: List[Tuple[str, str, str]] = []  # (label, company, url)
    for s in sources:
        label = s.get("label") or s.get("repo")
        md = _fetch_readme(s["repo"]) or ""
        links = [u for u in _extract_links(md) if _is_app_url(u)]
        for u in links:
            candidates.append((label, "", u))  # type: ignore

    conn = _ensure_db()
    c = conn.cursor()
    new_items: List[Dict] = []

    for label, company, url in candidates:
        rid = _sha1(url)
        try:
            c.execute(
                "INSERT INTO seen(id, company, url, first_seen) VALUES (?,?,?,?)",
                (rid, company or "", url, _now_iso()),
            )
            conn.commit()
            if not seed:
                new_items.append(
                    {"ts": _now_iso(), "label": label, "company": company, "url": url}
                )
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

    # Enrich + filter + notify
    kept = 0
    enriched_items: List[Dict] = []
    for it in new_items:
        det = _details_for(it["url"]) or {}
        title = det.get("title") or "New internship"
        loc = det.get("location") or ""
        urgent = _is_priority(it["company"], det, F)
        if not _passes_filters(it["company"], det, F):
            continue
        kept += 1
        line = f"[{it['label']}] {it['company'] or ''} — {title} ({loc}) -> {it['url']}"
        print(line)
        prio = 5 if urgent else 3
        notify_mac(
            f"{it['company'] or 'New internship'} — {title}",
            f"{it['label']}  {loc}\n{it['url']}",
        )
        phone_notify(
            f"{it['company'] or 'New internship'} — {title}",
            f"{it['label']}  {loc}\n{it['url']}",
            priority=prio,
        )
        enriched_items.append(
            {
                "ts": it["ts"],
                "label": it["label"],
                "company": it["company"],
                "title": title,
                "location": loc,
                "url": it["url"],
                "urgent": urgent,
            }
        )

    if enriched_items:
        _append_feed(enriched_items)

    if seed:
        print(f"Seeded {len(new_items)} items (no notifications on seed).")
    else:
        print(f"Found {len(new_items)} NEW items; notified on {kept} after filters.")
        if kept == 0 and notify_when_zero:
            msg_title = "Internship Watcher"
            msg_body = (
                f"{zero_prefix}: " if zero_prefix else ""
            ) + "You're all caught up ✅"
            print("[notify] zero kept -> sending caught-up notifications")
            notify_mac(msg_title, msg_body)
            phone_notify(msg_title, msg_body, priority=2)

    return (enriched_items, kept)
