"""Test: scrape_jobs_via_duckduckgo con requests (sin browser)."""
from search_scraper import scrape_jobs_via_duckduckgo

print("Probando DDG Lite via requests — QA / Argentina / remote...")
results = scrape_jobs_via_duckduckgo("QA", "Argentina", "remote")
print(f"\nResultados: {len(results)}")
for r in results[:10]:
    print(f"  • {r['title'][:55]} | {r['company'][:20]} | {r['url'][:65]}")






