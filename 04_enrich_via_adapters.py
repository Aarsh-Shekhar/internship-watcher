# 04_enrich_via_adapters.py
import json, time
from adapters import enrich_from_url

with open("internships.json", "r", encoding="utf-8") as f:
    rows = json.load(f)

enriched = []
for i, r in enumerate(rows, 1):
    d = enrich_from_url(r["url"])
    r["role"] = r.get("role") or d.get("title", "")
    r["location"] = r.get("location") or d.get("location", "")
    enriched.append(r)
    if i % 25 == 0:
        print(f"…{i}/{len(rows)}")
    time.sleep(0.1)  # be polite

with open("internships_enriched.json", "w", encoding="utf-8") as f:
    json.dump(enriched, f, indent=2)

print(f"Saved {len(enriched)} records to internships_enriched.json")
