import logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

from api_collector import fetch_api_jobs

results = fetch_api_jobs()
print(f"\nTotal: {len(results)} ofertas")
for r in results[:12]:
    print(f"  {r['title'][:50]:50s} | {r['company'][:20]:20s} | {r['job_url'][:65]}")

