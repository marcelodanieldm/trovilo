"""
ats_engine.py
-------------
Consulta directamente los endpoints JSON públicos de Greenhouse y Lever
sin necesidad de navegador — cero consumo de RAM de Playwright.

Funciones principales:
  fetch_greenhouse_jobs(company_id)  ->  list[dict]
  fetch_lever_jobs(company_id)       ->  list[dict]

Ambas retornan dicts con el esquema unificado del sistema:
  {
      'title':   str,   # título del puesto
      'company': str,   # company_id tal como llega (slug de la empresa)
      'job_url': str,   # URL directa a la oferta
      'location': str,  # ubicación reportada por el ATS
  }

APIs públicas (sin autenticación):
  Greenhouse  https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs
  Lever       https://api.lever.co/v0/postings/{company_id}?mode=json

Errores esperados:
  - 404: la empresa no tiene board público en ese ATS (se retorna [])
  - 429: rate-limit (se lanza RateLimitError para que el llamador espere)
  - Cualquier otro: se propaga como requests.HTTPError
"""
import logging

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs"
_LEVER_URL      = "https://api.lever.co/v0/postings/{company_id}?mode=json&limit=250"

# Headers mínimos — las APIs de ATS no necesitan user-agent completo
_HEADERS = {
    "Accept":       "application/json",
    "User-Agent":   "trovilo-ats-engine/1.0",
}

_TIMEOUT = 15  # segundos


# ---------------------------------------------------------------------------
# Excepción personalizada
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Lanzada cuando el ATS responde con HTTP 429 (Too Many Requests)."""


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _safe_str(value: object) -> str:
    """Convierte cualquier valor a str limpio; devuelve '' si es None/falsy."""
    return str(value).strip() if value else ""


def _location_greenhouse(job: dict) -> str:
    """Extrae la ubicación del objeto job de Greenhouse."""
    loc = job.get("location")
    if isinstance(loc, dict):
        return _safe_str(loc.get("name"))
    return _safe_str(loc)


def _location_lever(posting: dict) -> str:
    """Extrae la ubicación del objeto posting de Lever."""
    cats = posting.get("categories") or {}
    if isinstance(cats, dict):
        return _safe_str(cats.get("location") or cats.get("team") or cats.get("commitment"))
    return _safe_str(posting.get("workplaceType", ""))


# ---------------------------------------------------------------------------
# Función Greenhouse
# ---------------------------------------------------------------------------

def fetch_greenhouse_jobs(company_id: str) -> list[dict]:
    """
    Descarga las ofertas públicas de una empresa en Greenhouse.

    Endpoint:
        GET https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs

    Estructura de respuesta:
        { "jobs": [ {"title": str, "absolute_url": str,
                     "location": {"name": str}, ...}, ... ] }

    Parámetros:
        company_id -- slug de la empresa en Greenhouse (ej. "stripe", "airbnb")

    Retorna:
        Lista de dicts con esquema unificado, o [] si la empresa no tiene
        board público (404) o no hay ofertas activas.

    Lanza:
        RateLimitError  -- si el servidor responde 429
        requests.HTTPError -- para cualquier otro error HTTP ≥ 400
    """
    url = _GREENHOUSE_URL.format(company_id=company_id.strip())
    log.debug("Greenhouse GET %s", url)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("Greenhouse %s — error de red: %s", company_id, exc)
        return []

    if resp.status_code == 404:
        log.debug("Greenhouse %s — board no encontrado (404).", company_id)
        return []

    if resp.status_code == 429:
        raise RateLimitError(f"Greenhouse rate-limit para '{company_id}'")

    resp.raise_for_status()

    jobs: list[dict] = []
    for job in resp.json().get("jobs", []):
        if not isinstance(job, dict):
            continue

        title   = _safe_str(job.get("title"))
        job_url = _safe_str(job.get("absolute_url"))

        if not title or not job_url:
            continue

        jobs.append({
            "title":    title,
            "company":  company_id,
            "job_url":  job_url,
            "location": _location_greenhouse(job),
        })

    log.info("Greenhouse %-25s — %d oferta(s).", company_id, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Función Lever
# ---------------------------------------------------------------------------

def fetch_lever_jobs(company_id: str) -> list[dict]:
    """
    Descarga las ofertas públicas de una empresa en Lever.

    Endpoint:
        GET https://api.lever.co/v0/postings/{company_id}?mode=json&limit=250

    Estructura de respuesta (array directo, sin wrapper):
        [ {"text": str, "hostedUrl": str,
           "categories": {"location": str, ...}, ...}, ... ]

    Parámetros:
        company_id -- slug de la empresa en Lever (ej. "netflix", "figma")

    Retorna:
        Lista de dicts con esquema unificado, o [] si la empresa no tiene
        postings públicos (404) o no hay ofertas activas.

    Lanza:
        RateLimitError  -- si el servidor responde 429
        requests.HTTPError -- para cualquier otro error HTTP ≥ 400
    """
    url = _LEVER_URL.format(company_id=company_id.strip())
    log.debug("Lever GET %s", url)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("Lever %s — error de red: %s", company_id, exc)
        return []

    if resp.status_code == 404:
        log.debug("Lever %s — postings no encontrados (404).", company_id)
        return []

    if resp.status_code == 429:
        raise RateLimitError(f"Lever rate-limit para '{company_id}'")

    resp.raise_for_status()

    postings = resp.json()
    if not isinstance(postings, list):
        log.warning("Lever %s — respuesta inesperada (no es array).", company_id)
        return []

    jobs: list[dict] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue

        title   = _safe_str(posting.get("text"))
        job_url = _safe_str(posting.get("hostedUrl") or posting.get("applyUrl"))

        if not title or not job_url:
            continue

        jobs.append({
            "title":    title,
            "company":  company_id,
            "job_url":  job_url,
            "location": _location_lever(posting),
        })

    log.info("Lever     %-25s — %d oferta(s).", company_id, len(jobs))
    return jobs
