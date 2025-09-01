# 02_parse_table.py
import os, re, json, requests, urllib.parse

# Branches to try in order; we'll skip any that 404
BRANCH_CANDIDATES = ["dev", "main", "master"]
RAW = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/{branch}/README.md"

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


def fetch_raw(branch: str, cache_name: str) -> str | None:
    url = RAW.format(branch=branch)
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        print(f"[skip] README not found on branch '{branch}'")
        return None
    r.raise_for_status()
    md = r.text
    with open(cache_name, "w", encoding="utf-8") as f:
        f.write(md)
    return md


def normalize_images(line: str) -> str:
    # Convert [![...](img)](apply) → [Apply](apply) so we keep the apply URL
    line = re.sub(r"\[!\[[^\]]*\]\([^)]+\)\]\((https?://[^)]+)\)", r"[Apply](\1)", line)
    # Remove standalone images
    line = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", line)
    return line


def links_in_line(line: str):
    links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", line)  # markdown
    # also allow <a href="...">text</a>
    links += [
        (text, url)
        for url, text in re.findall(
            r'<a[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', line, flags=re.I
        )
    ]
    return links


def in_roles_section_title(line: str) -> bool:
    if not line.strip().startswith("#"):
        return False
    title = re.sub(r"^#+\s*", "", line).strip()
    # normalize away emojis/punctuation and compare by containment
    norm = re.sub(r"[^a-z0-9,& ]+", "", title.lower())
    for sec in SECTIONS:
        if re.sub(r"[^a-z0-9,& ]+", "", sec.lower()) in norm:
            return True
    return False


def guess_company_from_url(url: str) -> str:
    # fallback: take host + first path chunk as a crude "company"
    u = urllib.parse.urlparse(url)
    host = u.netloc.replace("www.", "")
    parts = [p for p in u.path.split("/") if p]
    if "lever.co" in host and parts:
        return parts[0].capitalize()
    if "greenhouse.io" in host and parts:
        return parts[0].capitalize()
    if "recruitee.com" in host:
        return host.split(".")[0].capitalize()
    return host


def parse_jobs_from_md(md: str):
    rows, in_section = [], False

    for raw in md.splitlines():
        line = raw.rstrip()

        # enter/leave sections
        if line.strip().startswith("#"):
            in_section = in_roles_section_title(line)
            continue

        if not in_section or "http" not in line:
            continue

        line = normalize_images(line)
        links = links_in_line(line)
        if not links:
            continue

        # app URL = last link pointing to a job domain
        app_url = ""
        for _, url in reversed(links):
            if any(d in url.lower() for d in ATS_DOMAINS):
                app_url = url.strip()
                break
        if not app_url or "top-list" in app_url:
            continue

        # company = first link text that isn't "Apply/Here"
        company = None
        for text, _ in links:
            if text and not re.search(r"apply|here|link|^image:", text, re.I):
                company = text.strip()
                break
        if not company:
            company = guess_company_from_url(app_url)

        # best-effort role/location from leftover text
        text_wo_links = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", line)
        text_wo_links = re.sub(r"\s+", " ", text_wo_links).strip(" -—–·|:")
        role, location = "", ""
        for sep in [" | ", " — ", " - ", " · ", " • "]:
            if sep in text_wo_links:
                left, right = text_wo_links.rsplit(sep, 1)
                if re.search(
                    r"(remote|hybrid|onsite|[A-Za-z]+\s*,\s*[A-Z]{2}|usa|canada|uk|europe)",
                    right,
                    re.I,
                ):
                    role, location = left.strip(), right.strip()
                    break

        rows.append(
            {"company": company, "role": role, "location": location, "url": app_url}
        )

    return rows


if __name__ == "__main__":
    all_rows = []
    for br in BRANCH_CANDIDATES:
        md = fetch_raw(br, f"readme_{br}.md")
        if md:
            all_rows += parse_jobs_from_md(md)

    # If nothing was found in sections (format changed), fall back to scanning entire file
    if not all_rows:
        for br in BRANCH_CANDIDATES:
            md = fetch_raw(br, f"readme_{br}.md")
            if not md:
                continue
            for line in md.splitlines():
                if "http" not in line:
                    continue
                if not any(d in line.lower() for d in ATS_DOMAINS):
                    continue
                # normalize and extract as above
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
                    company = guess_company_from_url(app_url)
                all_rows.append(
                    {"company": company, "role": "", "location": "", "url": app_url}
                )

    # de-dupe by (company,url)
    deduped, seen = [], set()
    for r in all_rows:
        key = (r["company"].strip().lower(), r["url"].strip())
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    print(f"Parsed {len(deduped)} job rows.")
    for r in deduped[:10]:
        print(f"- {r['company']} — {r['role']} ({r['location']}) -> {r['url']}")

    with open("internships.json", "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2)
    print("Saved to internships.json")
