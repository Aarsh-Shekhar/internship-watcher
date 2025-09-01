# 01_fetch_simplify.py
import os
import requests

API = "https://api.github.com/repos/SimplifyJobs/Summer2026-Internships/readme"
headers = {"Accept": "application/vnd.github.raw"}

# Optional: if you later create a GitHub token, this raises your rate limit
token = os.getenv("GITHUB_TOKEN")
if token:
    headers["Authorization"] = f"Bearer {token}"

print("Fetching README…")
resp = requests.get(API, headers=headers, timeout=20)
resp.raise_for_status()
md = resp.text

print(f"Downloaded {len(md)} characters.")
print("\n--- first 25 lines ---")
for i, line in enumerate(md.splitlines()[:25], start=1):
    print(f"{i:02d} {line}")

with open("readme_raw.md", "w", encoding="utf-8") as f:
    f.write(md)
print("\nSaved to readme_raw.md")
