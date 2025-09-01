# 03_watch_once.py
import os, re, json, sqlite3, hashlib, requests

from adapters import enrich_from_url, detect
from details_cache import get as cache_get, put as cache_put

BRANCH_CANDIDATES = ["dev", "main", "master"]
RAW = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/{branch}/README.md"
DB = "seen.db"

SECTIONS = [
    "Software Engineering Internship Roles",
    "Product Management Internship Roles",
    "Data Science, AI & Machine Learning Internship Roles",
    "Quantitative Finance Internship Roles",
    "Hardware Engineering Internship Roles",
    "Other Internship Roles",
]
ATS_DOMAINS = (
    "simplify.jobs",
    "lever.co",
    "greenhouse.io",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "recruitee.com",
    "bamboohr.com",
    "icims.com",
    "workable.com",
    "jobvite.com",
    "oraclecloud.com",
    "adp.com",
    "dayforcehcm.com",
    "workday.com",
)

NTFY_TOPIC = os.getenv(
    "NTFY_TOPIC", "aarsh-internships"
)  # set this env var if you want phone pushes


# ---- fetch & parse ----------------------------------------------------- #
def fetch_raw(branch: str) -> str | None:
    r = requests.get(RAW.format(branch=branch), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def normalize_images(line: str) -> str:
    line = re.sub(r"\[!\[[^\]]*\]\([^)]+\)\]\((https?://[^)]+)\)", r"[Apply](\1)", line)
    line = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", line)
    return line


def links_in_line(line: str):
    links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", line)
    links += [
        (text, url)
        for url, text in re.findall(
            r'<a[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', line, re.I
        )
    ]
    return links


def in_roles_section_title(line: str) -> bool:
    if not line.strip().startswith("#"):
        return False
    title = re.sub(r"^#+\s*", "", line).strip()
    norm = re.sub(r"[^a-z0-9,& ]+", "", title.lower())
    for sec in SECTIONS:
        if re.sub(r"[^a-z0-9,& ]+", "", sec.lower()) in norm:
            return True
    return False


def parse_jobs(md: str):
    rows, in_section = [], False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("#"):
            in_section = in_roles_section_title(line)
            continue
        if not in_section or "http" not in line:
            continue
        line = normalize_images(line)
        links = links_in_line(line)
        if not links:
            continue
        app_url = ""
        for _, url in reversed(links):
            if any(d in url.lower() for d in ATS_DOMAINS):
                app_url = url.strip()
                break
        if not app_url or "top-list" in app_url:
            continue
        company = None
        for text, _ in links:
            if text and not re.search(r"apply|here|link|^image:", text, re.I):
                company = text.strip()
                break
        if not company:
            from urllib.parse import urlparse

            u = urlparse(app_url)
            company = u.netloc.replace("www.", "")
        rows.append({"company": company, "url": app_url})
    return rows


# ---- DB + IDs ---------------------------------------------------------- #
def row_id(r):
    return hashlib.sha1(
        f"{r['company'].strip()}|{r['url'].strip()}".encode()
    ).hexdigest()


def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS seen(
        id TEXT PRIMARY KEY,
        company TEXT, url TEXT,
        first_seen TEXT DEFAULT (datetime('now'))
    )"""
    )
    conn.commit()
    conn.close()


# ---- notifications ----------------------------------------------------- #
def notify_mac(title: str, body: str):
    t = title.replace('"', '\\"')
    b = body.replace('"', '\\"')
    os.system(f"""osascript -e 'display notification "{b}" with title "{t}"' """)


def phone_notify(title: str, body: str, priority: int = 3):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            headers={"Title": title, "Priority": str(priority)},
            data=body.encode("utf-8"),
            timeout=8,
        )
    except Exception:
        pass


# ---- details cache wrapper -------------------------------------------- #
def details_for(url: str):
    d = cache_get(url)
    if d:
        return d
    d = enrich_from_url(url)
    cache_put(
        url, d.get("title", ""), d.get("location", ""), detect(url).get("provider", "")
    )
    return d


# ---- filters ----------------------------------------------------------- #
def load_filters():
    if os.path.exists("filters.json"):
        with open("filters.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "include_keywords": [],
        "exclude_keywords": [],
        "company_allowlist": [],
        "company_blocklist": [],
        "locations_any": [],
        "priority_companies": [],
        "priority_keywords": [],
    }


def textify(r, det):
    return " ".join(
        [r.get("company", ""), det.get("title", ""), det.get("location", "")]
    ).lower()


def passes_filters(r, det, F):
    t = textify(r, det)
    if F["company_blocklist"] and r["company"].lower() in [
        c.lower() for c in F["company_blocklist"]
    ]:
        return False
    if any(k.lower() in t for k in F["exclude_keywords"]):
        return False
    if F["company_allowlist"] and r["company"].lower() not in [
        c.lower() for c in F["company_allowlist"]
    ]:
        return False
    if F["include_keywords"] and not any(k.lower() in t for k in F["include_keywords"]):
        return False
    return True  # treat location as soft filter for now


def is_priority(r, det, F):
    t = textify(r, det)
    if r["company"] in F["priority_companies"]:
        return True
    return any(k.lower() in t for k in F["priority_keywords"])


# ---- main -------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    seed = "--seed" in sys.argv

    init_db()
    F = load_filters()

    # fetch + parse
    all_rows = []
    for br in BRANCH_CANDIDATES:
        md = fetch_raw(br)
        if md:
            all_rows += parse_jobs(md)

    # de-dupe (company,url)
    uniq, seen = [], set()
    for r in all_rows:
        k = (r["company"].strip().lower(), r["url"].strip())
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    # insert + collect new
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    new_items = []
    for r in uniq:
        rid = row_id(r)
        try:
            c.execute(
                "INSERT INTO seen(id, company, url) VALUES(?,?,?)",
                (rid, r["company"], r["url"]),
            )
            if not seed:
                new_items.append(r)
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()

    if seed:
        print(f"Seeded {len(uniq)} items (no notifications on seed).")
        raise SystemExit(0)

    # notify for new items (filtered + enriched)
    kept = 0
    for r in new_items:
        det = details_for(r["url"])
        if not passes_filters(r, det, F):
            continue
        title = det.get("title") or "New internship"
        loc = det.get("location") or ""
        urgent = is_priority(r, det, F)
        kept += 1
        print(f"- {r['company']} — {title} ({loc}) -> {r['url']}")
        notify_mac(
            f"{'URGENT: ' if urgent else ''}{r['company']} — {title}",
            f"{loc}\n{r['url']}",
        )
        phone_notify(
            f"{r['company']} — {title}",
            f"{loc}\n{r['url']}",
            priority=(5 if urgent else 3),
        )

    print(f"Found {len(new_items)} NEW items; notified on {kept} after filters.")
