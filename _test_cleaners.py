from cleaners import clean_and_verify_results

raw = [
    {"title": "QA Engineer | DuckDuckGo",  "company": "Acme",   "url": "https://boards.greenhouse.io/acme/jobs/123"},
    {"title": "Backend Dev - Job Board",    "company": "Corp",   "url": "https://jobs.lever.co/corp/abc-456"},
    {"title": "Settings page",             "company": "",       "url": "https://boards.greenhouse.io/settings"},
    {"title": "CAPTCHA",                   "company": "",       "url": "https://duckduckgo.com/captcha/"},
    {"title": "x",                         "company": "",       "url": "https://short"},           # URL < 15 chars
    {"title": "Dup job",                   "company": "Corp",   "url": "https://jobs.lever.co/corp/abc-456"},  # duplicado
    {"title": "DevOps | Bing",             "company": "TechCo", "url": "https://apply.workable.com/techco/j/XYZ123/"},
    {"title": "SRE - Google",              "company": "StartUp","url": "https://jobs.ashbyhq.com/startup/DEF789"},
    {"title": "Feedback form",             "company": "",       "url": "https://apply.workable.com/x/feedback"},
]

results = clean_and_verify_results(raw)
print(f"Input: {len(raw)}  →  Output: {len(results)}  (esperado: 4)\n")
for r in results:
    print(f"  ✓  {r['title']:<45} | {r['url'][:65]}")
