# 04_enrich_via_adapters_fast.py
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from adapters import enrich_from_url, detect
from details_cache import get as cache_get, put as cache_put

MAX_WORKERS = 16  # lower if your network gets grumpy

with open("internships.json", "r", encoding="utf-8") as f:
    rows = json.load(f)

to_fetch = [r for r in rows if not cache_get(r["url"])]
print(f"{len(to_fetch)} need enrichment; {len(rows) - len(to_fetch)} already cached.")


def task(r):
    url = r["url"]
    d = enrich_from_url(url)
    prov = detect(url).get("provider", "")
    cache_put(url, d.get("title", ""), d.get("location", ""), prov)
    return url


i = 0
if to_fetch:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(task, r) for r in to_fetch]
        for fut in as_completed(futures):
            fut.result()
            i += 1
            if i % 25 == 0 or i == len(to_fetch):
                print(f"...{i}/{len(to_fetch)}")


def merge(r):
    cached = cache_get(r["url"]) or {}
    r["role"] = r.get("role") or cached.get("title", "")
    r["location"] = r.get("location") or cached.get("location", "")
    return r


enriched = [merge(r) for r in rows]
with open("internships_enriched.json", "w", encoding="utf-8") as f:
    json.dump(enriched, f, indent=2)
print(f"Saved {len(enriched)} to internships_enriched.json")
