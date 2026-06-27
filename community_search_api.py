"""
community_search_api.py
-----------------------
API local mínima para que index.html pueda buscar/scrapear fuentes públicas
(comunidad y nicho) desde la sección Buscador.

Uso:
    python community_search_api.py

Endpoint:
    GET /search?q=python&location=remote&source=all&limit=80

Respuesta:
    {
      "ok": true,
      "count": 10,
      "jobs": [
        {"title", "company", "job_url", "location", "sent_at", "not_on_linkedin", "source"}
      ]
    }
"""
import json
import logging
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from feed_ingester import (
    fetch_arbeitnow_jobs,
    fetch_cryptojobslist_jobs,
    fetch_remoteok_jobs,
    fetch_remotive_jobs,
    fetch_weworkremotely_jobs,
)
from rocket_feeder import fetch_rocketship_jobs


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("community_search_api")

HOST = os.environ.get("TROVILO_SEARCH_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("TROVILO_SEARCH_API_PORT", "8765"))

SOURCE_FETCHERS = {
    "remotive": fetch_remotive_jobs,
    "wwr": fetch_weworkremotely_jobs,
    "remoteok": fetch_remoteok_jobs,
    "arbeitnow": fetch_arbeitnow_jobs,
    "cryptojobslist": fetch_cryptojobslist_jobs,
    "rocketship": fetch_rocketship_jobs,
}


def _clean(value: object) -> str:
    return str(value or "").strip()


_SYNONYMS: dict[str, list[str]] = {
    "qa":         ["quality", "testing", "tester", "qa"],
    "qe":         ["quality", "testing", "qe"],
    "fe":         ["frontend", "front-end", "fe"],
    "be":         ["backend", "back-end", "be"],
    "js":         ["javascript", "js"],
    "ts":         ["typescript", "ts"],
    "ml":         ["machine learning", "ml"],
    "ai":         ["artificial intelligence", "ai", "machine learning"],
    "devops":     ["devops", "dev ops", "sre", "platform engineer"],
    "sre":        ["sre", "reliability", "devops"],
    "pm":         ["product manager", "pm"],
    "fullstack":  ["full stack", "full-stack", "fullstack"],
    "fullsatck":  ["full stack", "full-stack", "fullstack"],
}


def _matches_query(job: dict, query: str) -> bool:
    if not query:
        return True
    title = _clean(job.get("title")).lower()
    # Cada token del query debe aparecer en el título (con expansión de sinónimos)
    for raw_token in query.split():
        token = raw_token.strip().lower()
        if not token:
            continue
        expansions = _SYNONYMS.get(token, [token])
        if not any(exp in title for exp in expansions):
            return False
    return True


def _matches_location(job: dict, location: str) -> bool:
    if not location:
        return True
    location = location.lower()
    haystack = " ".join([_clean(job.get("location")), _clean(job.get("title")), _clean(job.get("job_url"))]).lower()
    aliases = {
        "remote": ("remote", "worldwide", "anywhere"),
        "latam": ("latam", "latin america", "argentina", "uruguay", "colombia", "mexico", "brasil", "brazil"),
        "brasil": ("brasil", "brazil"),
        "mexico": ("mexico", "méxico"),
    }
    needles = aliases.get(location, (location,))
    return any(needle in haystack for needle in needles)


def _dedupe(jobs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        url = _clean(job.get("job_url"))
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(job)
    return unique


def search_community_jobs(query: str = "", location: str = "", source: str = "all", limit: int = 80) -> list[dict]:
    """Ejecuta conectores públicos y filtra resultados en memoria."""
    selected = list(SOURCE_FETCHERS.items()) if source == "all" else [(source, SOURCE_FETCHERS[source])]
    now_iso = datetime.now(timezone.utc).isoformat()
    jobs: list[dict] = []

    for source_name, fetcher in selected:
        try:
            if source_name == "rocketship":
                source_jobs = fetcher(query="", limit=limit)  # ?q= no filtra en su SSR
            elif source_name == "arbeitnow":
                source_jobs = fetcher(query=query)
            else:
                source_jobs = fetcher()
        except Exception as exc:
            log.warning("%s — error al scrapear fuente: %s", source_name, exc)
            continue

        for job in source_jobs:
            if not _matches_query(job, query) or not _matches_location(job, location):
                continue
            jobs.append(
                {
                    "title": _clean(job.get("title")),
                    "company": _clean(job.get("company")),
                    "job_url": _clean(job.get("job_url")),
                    "location": _clean(job.get("location") or "Remote"),
                    "sent_at": now_iso,
                    "not_on_linkedin": True,
                    "source": source_name,
                }
            )

    return _dedupe(jobs)[:limit]


class CommunitySearchHandler(BaseHTTPRequestHandler):
    server_version = "TroviloCommunitySearch/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "service": "community_search_api"})
            return

        if parsed.path != "/search":
            self._send_json(404, {"ok": False, "error": "Endpoint no encontrado"})
            return

        query = _clean(params.get("q", [""])[0])
        location = _clean(params.get("location", [""])[0])
        source = _clean(params.get("source", ["all"])[0]).lower() or "all"
        limit_raw = _clean(params.get("limit", ["80"])[0])

        if source != "all" and source not in SOURCE_FETCHERS:
            self._send_json(400, {"ok": False, "error": f"Fuente desconocida: {source}"})
            return

        try:
            limit = max(1, min(int(limit_raw), 200))
        except ValueError:
            limit = 80

        jobs = search_community_jobs(query=query, location=location, source=source, limit=limit)
        self._send_json(200, {"ok": True, "count": len(jobs), "jobs": jobs})

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def run() -> None:
    server = ThreadingHTTPServer((HOST, PORT), CommunitySearchHandler)
    log.info("Community Search API escuchando en http://%s:%d", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    run()
