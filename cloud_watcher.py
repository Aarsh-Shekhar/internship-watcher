# cloud_watcher.py
import os, re, json, time, datetime, sqlite3, requests
from typing import List, Dict, Any

# ------------------------
# Helpers
# ------------------------


def utc_now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ------------------------
# Fetch sources (your existing multi-source scanner)
# ------------------------


def load_sources() -> List[Dict[str, str]]:
    with open("sources.json", "r", encoding="utf-8") as f:
        return json.load(f)


UA = {
    "User-Agent": "internship-watcher/1.0 (+https://github.com/)",
    "Accept": "application/json, text/*;q=0.8, */*;q=0.5",
}


def fetch_source(s: Dict[str, str]) -> List[Dict[str, str]]:
    label = s.get("label", "source")
    url = s["url"]
    # Minimal adapters: treat as a page that has job links we already parsed earlier.
    # Here, just yield a single “check” to prove pipeline; your real adapters would go here.
    return [
        {
            "source": label,
            "company": s.get("company", "-"),
            "title": s.get("title", "New internship"),
            "location": s.get("location", ""),
            "url": url,
            "ts": utc_now(),
        }
    ]


def scan_all_sources() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for s in load_sources():
        try:
            rows.extend(fetch_source(s))
        except Exception as e:
            print(f"[warn] fetch failed for {s.get('label', s.get('url'))}: {e}")
    return rows


# ------------------------
# Gist helpers
# ------------------------


def _gist_get(gist_id: str, token: str) -> requests.Response:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.get(
        f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=30
    )


def _gist_put(gist_id: str, token: str, jsonl: str) -> requests.Response:
    """
    Write/replace file cloud_feed.jsonl in the gist.
    """
    payload = {"files": {"cloud_feed.jsonl": {"content": jsonl}}}
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers=headers,
        json=payload,
        timeout=30,
    )


def read_existing_from_gist(gist_id: str, token: str) -> List[Dict[str, Any]]:
    r = _gist_get(gist_id, token)
    if r.status_code != 200:
        raise RuntimeError(f"Gist not reachable (status {r.status_code})")
    data = r.json()
    files = data.get("files", {})
    file = files.get("cloud_feed.jsonl")
    if not file:
        # empty gist or missing file
        return []
    raw_url = file.get("raw_url")
    if not raw_url:
        return []
    rr = requests.get(raw_url, timeout=30)
    rr.raise_for_status()
    lines = []
    for line in rr.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except Exception:
            pass
    return lines


# ------------------------
# MAIN
# ------------------------


def main() -> None:
    print("Cloud watcher: start")
    gist_id = os.environ.get("GIST_ID", "").strip()
    token = os.environ.get("GIST_TOKEN", "").strip()

    # Step 1: assemble candidate new rows from sources
    new_rows = scan_all_sources()

    # Step 2: try reading existing feed from Gist
    use_gist = bool(gist_id)
    existing: List[Dict[str, Any]] = []
    if use_gist:
        try:
            existing = read_existing_from_gist(gist_id, token)
            print(f"Gist read OK — existing lines: {len(existing)}")
        except Exception as e:
            print(f"[gist] preflight failed: {e} -> falling back to repo file")
            use_gist = False

    # Step 3: if no gist, read repo file
    if not use_gist:
        existing = read_jsonl("cloud_feed.jsonl")
        print(f"Repo feed read — existing lines: {len(existing)}")

    # Step 4: append (no dedupe here; keep it simple—dedupe could be added)
    out = existing + new_rows

    # Step 5: write
    if use_gist:
        jsonl = "\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n"
        r = _gist_put(gist_id, token, jsonl)
        r.raise_for_status()
        print(f"Wrote {len(out)} lines to Gist.")
    else:
        write_jsonl("cloud_feed.jsonl", out)
        print(f"Wrote {len(out)} lines to repo file cloud_feed.jsonl.")


if __name__ == "__main__":
    main()
