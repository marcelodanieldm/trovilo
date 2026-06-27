"""
feed_ingester.py
----------------
Extractor rápido de feeds públicos de empleo tech/remoto.

Consume fuentes sin Playwright:
  - Remotive API (JSON público)
  - We Work Remotely RSS (XML público)

Normaliza ambas fuentes al schema interno:
  {'title', 'company', 'job_url', 'location'}

Uso:
    python feed_ingester.py
"""
import json
import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("feed_ingester")

load_dotenv()

REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
WWR_PROGRAMMING_RSS_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
REMOTEOK_API_URL = "https://remoteok.com/api"
ARBEITNOW_API_URL = "https://www.arbeitnow.com/api/job-board-api"
CRYPTOJOBSLIST_ATOM_URL = "https://cryptojobslist.com/atom.xml"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, application/xml, text/xml, */*;q=0.8",
}

_TITLE_KEYWORDS = re.compile(
    r"\b(qa|quality[\s\-]*assurance|automation|python|react|node\.?js|typescript|javascript|playwright|cypress)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 20) -> bytes:
    """Ejecuta un GET rápido con urllib y devuelve bytes crudos."""
    req = Request(url, headers=HTTP_HEADERS, method="GET")
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def _clean_text(value: str | None) -> str:
    """Limpia entidades HTML, saltos de línea y whitespace extra."""
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _sanitize_url(url: str | None) -> str:
    """Devuelve una URL canónica sin query params ni fragmentos de tracking."""
    clean = _clean_text(url)
    if not clean:
        return ""

    try:
        parsed = urlparse(clean)
        if not parsed.scheme or not parsed.netloc:
            return clean
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    except Exception:
        return clean


def _matches_keywords(job: dict) -> bool:
    """Retorna True si la oferta contiene una keyword técnica relevante."""
    haystack = " ".join(
        [
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("job_url", ""),
        ]
    )
    return bool(_TITLE_KEYWORDS.search(haystack))


def _dedupe_jobs(jobs: Iterable[dict]) -> list[dict]:
    """Deduplica por job_url preservando el orden original."""
    seen: set[str] = set()
    unique: list[dict] = []

    for job in jobs:
        job_url = job.get("job_url")
        if not job_url or job_url in seen:
            continue
        seen.add(job_url)
        unique.append(job)

    return unique


# ---------------------------------------------------------------------------
# Remotive API
# ---------------------------------------------------------------------------

def fetch_remotive_jobs() -> list[dict]:
    """
    Consume la API pública de Remotive y normaliza los items.

    Campos fuente:
      - title
      - company_name
      - url
      - candidate_required_location

    Output:
      [{'title': title, 'company': company, 'job_url': url, 'location': location}, ...]
    """
    try:
        payload = _http_get(REMOTIVE_API_URL)
        parsed = json.loads(payload.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.error("Remotive API: error al descargar/parsear feed: %s", exc)
        return []

    jobs = []
    for item in parsed.get("jobs", []):
        title = _clean_text(item.get("title"))
        company = _clean_text(item.get("company_name"))
        job_url = _sanitize_url(item.get("url"))
        location = _clean_text(item.get("candidate_required_location") or "Remote")

        if not all([title, company, job_url]):
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": job_url,
                "location": location,
            }
        )

    log.info("Remotive API: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


# Alias legacy para compatibilidad con imports previos.
fetch_remotive_api = fetch_remotive_jobs


# ---------------------------------------------------------------------------
# We Work Remotely RSS
# ---------------------------------------------------------------------------

def _parse_wwr_title(raw_title: str, author: str | None = None) -> tuple[str, str]:
    """
    Extrae company/title desde títulos RSS de WWR.

    Formatos observados:
      - "Company: Job Title"
      - "Job Title at Company"
      - company en author/dc:creator
    """
    title = _clean_text(raw_title)
    clean_author = _clean_text(author)

    if ":" in title:
        company, job_title = title.split(":", 1)
        return _clean_text(company), _clean_text(job_title)

    if clean_author:
        return clean_author, title

    match = re.match(r"(?P<title>.+?)\s+at\s+(?P<company>.+)$", title, flags=re.IGNORECASE)
    if match:
        return _clean_text(match.group("company")), _clean_text(match.group("title"))

    return "We Work Remotely", title


def _extract_location_from_description(description: str) -> str:
    """Extrae una ubicación aproximada desde el description RSS si está presente."""
    text = _clean_text(re.sub(r"<[^>]+>", " ", description or ""))
    match = re.search(r"\b(Location|Region):\s*([^|•\n]+)", text, flags=re.IGNORECASE)
    if match:
        return _clean_text(match.group(2))
    return "Remote"


def fetch_weworkremotely_jobs() -> list[dict]:
    """
    Consume el RSS público de We Work Remotely y normaliza los items.

    Output:
      [{'title': title, 'company': company, 'job_url': url, 'location': location}, ...]
    """
    try:
        xml_bytes = _http_get(WWR_PROGRAMMING_RSS_URL)
        root = ET.fromstring(xml_bytes)
    except (HTTPError, URLError, TimeoutError, ET.ParseError) as exc:
        log.error("WWR RSS: error al descargar/parsear feed: %s", exc)
        return []

    jobs = []
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")

    for item in items:
        raw_title = item.findtext("title")
        link = _sanitize_url(item.findtext("link"))
        description = item.findtext("description") or ""
        author = (
            item.findtext("author")
            or item.findtext("{http://purl.org/dc/elements/1.1/}creator")
        )

        company, title = _parse_wwr_title(raw_title or "", author)
        location = _extract_location_from_description(description)

        if not all([title, company, link]):
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": link,
                "location": location,
            }
        )

    log.info("WWR RSS: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


# Alias legacy para compatibilidad con imports previos.
fetch_wwr_rss = fetch_weworkremotely_jobs


# ---------------------------------------------------------------------------
# RemoteOK API
# ---------------------------------------------------------------------------

def fetch_remoteok_jobs() -> list[dict]:
    """
    Consume RemoteOK API y normaliza los items.

    RemoteOK devuelve un array JSON cuyo primer elemento puede ser un disclaimer
    legal. Se ignora todo elemento que no tenga campos de oferta.

    Campos fuente:
      - position
      - company
      - url
      - tags
    """
    try:
        payload = _http_get(REMOTEOK_API_URL)
        parsed = json.loads(payload.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.error("RemoteOK API: error al descargar/parsear feed: %s", exc)
        return []

    jobs = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue

        title = _clean_text(item.get("position"))
        company = _clean_text(item.get("company"))
        job_url = _sanitize_url(item.get("url") or item.get("apply_url"))
        tags = item.get("tags") or []
        location = _clean_text(item.get("location") or "Remote")

        if isinstance(tags, list) and tags:
            location = f"{location} | {' '.join(_clean_text(tag) for tag in tags if tag)}"

        if not all([title, company, job_url]):
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": job_url,
                "location": location,
            }
        )

    log.info("RemoteOK API: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Arbeitnow API
# ---------------------------------------------------------------------------

def fetch_arbeitnow_jobs() -> list[dict]:
    """
    Consume Arbeitnow job-board API y normaliza los items.

    Campos fuente:
      - title
      - company_name
      - url
      - tags
    """
    try:
        payload = _http_get(ARBEITNOW_API_URL)
        parsed = json.loads(payload.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.error("Arbeitnow API: error al descargar/parsear feed: %s", exc)
        return []

    jobs = []
    for item in parsed.get("data", []) if isinstance(parsed, dict) else []:
        title = _clean_text(item.get("title"))
        company = _clean_text(item.get("company_name"))
        job_url = _sanitize_url(item.get("url"))
        tags = item.get("tags") or []
        location = _clean_text(item.get("location") or item.get("job_types") or "Remote")

        if isinstance(tags, list) and tags:
            location = f"{location} | {' '.join(_clean_text(tag) for tag in tags if tag)}"

        if not all([title, company, job_url]):
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": job_url,
                "location": location,
            }
        )

    log.info("Arbeitnow API: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# CryptoJobsList Atom
# ---------------------------------------------------------------------------

def fetch_cryptojobslist_jobs() -> list[dict]:
    """
    Consume CryptoJobsList Atom feed y normaliza los entries.

    Campos fuente:
      - title
      - author/name
      - link[@href]
    """
    try:
        xml_bytes = _http_get(CRYPTOJOBSLIST_ATOM_URL)
        root = ET.fromstring(xml_bytes)
    except (HTTPError, URLError, TimeoutError, ET.ParseError) as exc:
        log.error("CryptoJobsList Atom: error al descargar/parsear feed: %s", exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns) or root.findall(".//entry")

    jobs = []
    for entry in entries:
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns) or entry.findtext("title"))
        author = entry.find("atom:author", ns)
        if author is None:
            author = entry.find("author")
        company = ""
        if author is not None:
            company = _clean_text(author.findtext("atom:name", default="", namespaces=ns) or author.findtext("name"))

        link_el = entry.find("atom:link", ns)
        if link_el is None:
            link_el = entry.find("link")
        raw_link = link_el.get("href") if link_el is not None else ""
        job_url = _sanitize_url(raw_link)
        location = "Remote | Crypto"

        if not all([title, company, job_url]):
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": job_url,
                "location": location,
            }
        )

    log.info("CryptoJobsList Atom: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Pipeline local: unificar, filtrar y persistir
# ---------------------------------------------------------------------------

def fetch_public_feed_jobs(filter_keywords: bool = False) -> list[dict]:
    """
    Retorna Remotive + WWR como lista unificada de dicts del schema interno.

    Si filter_keywords=True, aplica el filtro local técnico antes de retornar.
    """
    raw_jobs = [
        *fetch_remotive_jobs(),
        *fetch_weworkremotely_jobs(),
        *fetch_remoteok_jobs(),
        *fetch_arbeitnow_jobs(),
        *fetch_cryptojobslist_jobs(),
    ]
    deduped = _dedupe_jobs(raw_jobs)
    jobs = [job for job in deduped if _matches_keywords(job)] if filter_keywords else deduped

    log.info(
        "Feeds públicos: %d cruda(s), %d única(s), %d retornada(s).",
        len(raw_jobs), len(deduped), len(jobs),
    )
    return jobs


def bulk_upsert_sent_jobs(jobs: list[dict]) -> int:
    """Hace bulk upsert en sent_jobs usando el schema real de Supabase."""
    if not jobs:
        log.info("Feeds públicos: no hay ofertas relevantes para guardar.")
        return 0

    from notifier import supabase

    now_iso = datetime.now(timezone.utc).isoformat()
    records = [
        {
            "title": job["title"],
            "company": job["company"],
            "job_url": job["job_url"],
            "sent_at": now_iso,
            # Estos feeds son externos a LinkedIn; se marcan como exclusivas por default.
            "not_on_linkedin": True,
        }
        for job in jobs
    ]

    try:
        supabase.table("sent_jobs").upsert(records, on_conflict="job_url").execute()
    except Exception as exc:
        log.error("Feeds públicos: error en upsert sent_jobs: %s", exc)
        return 0

    log.info("Feeds públicos: upsert de %d oferta(s) completado.", len(records))
    return len(records)


def run() -> int:
    """Ejecuta ingesta completa y retorna cantidad de ofertas enviadas al upsert."""
    jobs = fetch_public_feed_jobs(filter_keywords=True)
    return bulk_upsert_sent_jobs(jobs)


if __name__ == "__main__":
    run()
