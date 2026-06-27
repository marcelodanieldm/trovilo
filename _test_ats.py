import logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

from ats_engine import fetch_greenhouse_jobs, fetch_lever_jobs, RateLimitError

# Empresas reales conocidas con boards públicos
GH_COMPANIES  = ["notion",   "vercel",  "stripe"]
LV_COMPANIES  = ["netflix",  "figma",   "linear"]

print("=== Greenhouse ===")
for cid in GH_COMPANIES:
    try:
        jobs = fetch_greenhouse_jobs(cid)
        for j in jobs[:3]:
            print(f"  {j['title'][:45]:45s} | {j['location'][:20]:20s} | {j['job_url'][:60]}")
    except RateLimitError as e:
        print(f"  [429] {e}")

print("\n=== Lever ===")
for cid in LV_COMPANIES:
    try:
        jobs = fetch_lever_jobs(cid)
        for j in jobs[:3]:
            print(f"  {j['title'][:45]:45s} | {j['location'][:20]:20s} | {j['job_url'][:60]}")
    except RateLimitError as e:
        print(f"  [429] {e}")
