# cloud_watcher.py
"""
Reads your enriched/local listings and appends any NEW urls to a JSONL feed
stored in a GitHub Gist. Designed to run on GitHub Actions or locally.

Env:
  GIST_ID      (required)
  GIST_TOKEN   (required for secret gists or to write to any gist)
"""
from __future__ import annotations
import os, json, re, sys, datetime, hashlib
from typing import List, Dict
import requests

GIST_FILE = "cloud_feed.jsonl"


def _utc_now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_json_file(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []


def _load_local_pool() -> List[Dict]:
    """
    Prefer enriched; fall back to basic internships.json. Each record normalized to:
      { ts, source, company, title, location, url }
    """
    rows = []
    enriched = _read_json_file("internships_enriched.json")
    basic = _read_json_file("internships.json")

    def norm(r: Dict) -> Dict:
        url = r.get("url", "").strip()
        if not url:
            return {}
        return {
            "ts": r.get("ts") or _utc_now(),
            "source": r.get("source")
            or ("Simplify 2026 Internships" if "SimplifyJobs" in url else "unknown"),
            "company": r.get("company", "").strip(),
            "title": (r.get("title") or r.get("role") or "New internship").strip(),
            "location": r.get("location", "").strip(),
            "url": url,
        }

    src = enriched if enriched else basic
    for r in src:
        n = norm(r)
        if n:
            rows.append(n)
    return rows


def _gist_get(gist_id: str, token: str) -> Dict:
    r = requests.get(
        f"https://api.github.com/gists/{gist_id}",
        headers={"Authorization": f"token {token}"} if token else {},
    )
    r.raise_for_status()
    return r.json()


def _gist_put(gist_id: str, token: str, content: str) -> None:
    payload = {"files": {GIST_FILE: {"content": content}}}
    r = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
        json=payload,
    )
    r.raise_for_status()


def _parse_jsonl(s: str) -> List[Dict]:
    out = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            # ignore broken lines
            pass
    return out


def main() -> None:
    gist_id = os.getenv("GIST_ID") or os.getenv("CLOUD_GIST_ID")
    token = os.getenv("GIST_TOKEN") or os.getenv("CLOUD_GIST_TOKEN")
    if not gist_id:
        print("ERROR: GIST_ID env var missing", file=sys.stderr)
        sys.exit(1)

    # Load existing feed from gist (if present)
    existing_urls = set()
    existing_lines = []
    try:
        g = _gist_get(gist_id, token or "")
        files = g.get("files", {})
        if GIST_FILE in files and "content" in files[GIST_FILE]:
            existing_lines = _parse_jsonl(files[GIST_FILE]["content"] or "")
            for row in existing_lines:
                url = (row.get("url") or "").strip()
                if url:
                    existing_urls.add(url)
        else:
            # if the file isn't present yet, start fresh
            existing_lines = []
    except requests.HTTPError as e:
        print(f"WARNING: could not read gist: {e}", file=sys.stderr)
        existing_lines = []

    # Build local pool and diff
    pool = _load_local_pool()
    new_rows = [r for r in pool if r.get("url") and r["url"] not in existing_urls]

    if not new_rows:
        print("No new items to append to gist.")
        return

    # Make JSONL string: keep existing + append new
    out_lines = existing_lines[:]  # keep order
    for r in new_rows:
        # ensure ts/source present
        r = {
            "ts": r.get("ts") or _utc_now(),
            "source": r.get("source") or "unknown",
            "company": r.get("company", ""),
            "title": r.get("title", "New internship"),
            "location": r.get("location", ""),
            "url": r["url"],
        }
        out_lines.append(r)

    jsonl = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out_lines)
    _gist_put(gist_id, token or "", jsonl)
    print(f"Appended {len(new_rows)} new items to Gist (total {len(out_lines)}).")


if __name__ == "__main__":
    main()
