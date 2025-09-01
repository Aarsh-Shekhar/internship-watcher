# cloud_watcher.py
import os, json, datetime, requests
from typing import List, Dict, Any


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


def load_sources() -> List[Dict[str, str]]:
    try:
        with open("sources.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def fetch_source(s: Dict[str, str]) -> Dict[str, str]:
    """
    Very light record builder. We only require a URL to anchor the row.
    If you want full scraping here, wire this to your real adapters later.
    """
    label = s.get("label") or s.get("name") or "source"
    url = s.get("url") or s.get("rss") or s.get("feed") or s.get("endpoint")
    if not url:
        raise KeyError("url")

    return {
        "ts": utc_now(),
        "source": label,
        "company": s.get("company", "-"),
        "title": s.get("title", "New internship"),
        "location": s.get("location", ""),
        "url": url,
    }


def scan_all_sources() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for s in load_sources():
        try:
            rows.append(fetch_source(s))
        except Exception as e:
            print(
                f"[warn] fetch failed for {s.get('label', s.get('name', 'source'))}: {e!s}"
            )
    return rows


# ---------- Gist helpers ----------
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
    r.raise_for_status()
    data = r.json()
    file = (data.get("files") or {}).get("cloud_feed.jsonl")
    if not file:
        return []
    raw_url = file.get("raw_url")
    if not raw_url:
        return []
    rr = requests.get(raw_url, timeout=30)
    rr.raise_for_status()
    rows: List[Dict[str, Any]] = []
    for line in rr.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def main() -> None:
    print("Cloud watcher: start")

    gist_id = os.environ.get("GIST_ID", "").strip()
    token = os.environ.get("GIST_TOKEN", "").strip()

    # Build new rows (skip sources missing URLs, but don't crash)
    new_rows = scan_all_sources()

    # Prefer gist; if we can't reach or write it, fall back to repo file
    use_gist = bool(gist_id)
    existing: List[Dict[str, Any]] = []

    if use_gist:
        try:
            existing = read_existing_from_gist(gist_id, token)
            print(f"Gist read OK — existing lines: {len(existing)}")
        except Exception as e:
            print(f"[gist] GET failed ({e}); falling back to repo file")
            use_gist = False

    if not use_gist:
        existing = read_jsonl("cloud_feed.jsonl")
        print(f"Repo feed read — existing lines: {len(existing)}")

    out = existing + new_rows
    print(f"Prepared {len(new_rows)} new rows; writing total {len(out)} lines")

    if use_gist:
        try:
            jsonl = "\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n"
            r = _gist_put(gist_id, token, jsonl)
            r.raise_for_status()
            print(f"Gist write OK — total lines now {len(out)}")
            return
        except Exception as e:
            print(f"[gist] PUT failed ({e}); falling back to repo file")

    # Fallback: write to repo file (workflow will commit it)
    try:
        write_jsonl("cloud_feed.jsonl", out)
        print(f"Repo feed write OK — total lines now {len(out)}")
    except Exception as e:
        # Even in failure, exit gracefully so the workflow can still complete
        print(f"[repo] write failed: {e}")


if __name__ == "__main__":
    main()
