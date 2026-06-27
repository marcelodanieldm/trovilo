"""
api_collector.py
----------------
Recolecta ofertas de trabajo desde APIs públicas sin necesidad de scraping:
RemoteOK y Remotive ofrecen endpoints JSON con datos estructurados.

Función principal:
  fetch_api_jobs(tech_keywords) -> list[dict]
      Consulta ambas APIs, normaliza el payload al esquema de 'sent_jobs'
      y filtra por keywords de tecnología/rol.

Esquema de salida (columnas de sent_jobs):
  {'title': str, 'company': str, 'job_url': str, 'sent_at': str ISO-8601}

Diseño:
  - Cada fuente se envuelve en un try/except independiente: si una API
    falla, la otra sigue procesándose sin interrumpir el flujo.
  - Sin dependencias extra: solo usa `requests`, ya incluido en requirements.txt.
  - Sincrónico (no async) para ser compatible con el orquestador existente.
    Para uso async, envolver con asyncio.to_thread(fetch_api_jobs, ...).
"""
import logging
import html as _html
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints de las APIs públicas
# ---------------------------------------------------------------------------

# RemoteOK: endpoint general + endpoints por tag de tecnología/rol.
# Se consultan en paralelo para mayor cobertura; los resultados se deduplicarán
# por job_url antes de retornar.
_REMOTEOK_BASE    = "https://remoteok.com/api"
_REMOTEOK_TAGS    = ["python", "react", "qa", "testing", "automation"]
_REMOTEOK_TAG_URL = "https://remoteok.com/api?tag={tag}"

# Remotive: varias categorías relevantes para el perfil buscado.
# Nota: Remotive no tiene categoría dedicada a 'qa' — se cubre con
# software-development (que incluye QA/testing) y data.
_REMOTIVE_CATEGORIES = [
    "software-development",
    "data",
]
_REMOTIVE_BASE = "https://remotive.com/api/remote-jobs?category={category}"

# Headers que imitan un navegador real para evitar bloqueos 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9",
    "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
}

# Keywords de filtrado por defecto (en minúsculas para comparación rápida)
_DEFAULT_KEYWORDS: frozenset[str] = frozenset({
    "qa", "automation", "tester", "testing",
    "python", "react",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Timestamp ISO-8601 UTC del momento actual."""
    return datetime.now(timezone.utc).isoformat()


def _title_matches(title: str, keywords: frozenset[str]) -> bool:
    """True si el título contiene al menos una keyword (case-insensitive)."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in keywords)


# ---------------------------------------------------------------------------
# Fetchers por fuente
# ---------------------------------------------------------------------------

def _fetch_remoteok(keywords: frozenset[str]) -> list[dict]:
    """
    Obtiene ofertas de RemoteOK consultando el feed general más un endpoint
    por cada tag de tecnología relevante (?tag=python, ?tag=qa, etc.).

    Los resultados de todos los endpoints se combinan y se deduplicarán
    por job_url en el nivel superior de fetch_api_jobs().

    Campos del job:
      - position / title : título del puesto (RemoteOK usa 'position')
      - company          : nombre de la empresa
      - url              : URL directa a la oferta
    """
    # Construir la lista de URLs a consultar: feed general + un URL por tag
    urls = [_REMOTEOK_BASE] + [
        _REMOTEOK_TAG_URL.format(tag=tag) for tag in _REMOTEOK_TAGS
    ]

    jobs: list[dict] = []
    seen_urls_local: set[str] = set()   # dedup entre endpoints de tags
    now = _now_iso()

    for api_url in urls:
        try:
            resp = requests.get(api_url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("RemoteOK %s — error: %s", api_url, exc)
            continue

        for item in resp.json():
            if not isinstance(item, dict):
                continue

            # RemoteOK usa 'position'; fallback a 'title' para versiones antiguas
            title   = (item.get("position") or item.get("title") or "").strip()
            job_url = (item.get("url") or "").strip()

            if not title or not job_url:
                continue  # elemento de metadata o entrada incompleta

            if not _title_matches(title, keywords):
                continue

            # Evitar duplicados del mismo job en múltiples tags
            if job_url in seen_urls_local:
                continue
            seen_urls_local.add(job_url)

            jobs.append({
                "title":   _html.unescape(title),
                "company": _html.unescape((item.get("company") or "").strip()),
                "job_url": job_url,
                "sent_at": now,
            })

    return jobs


def _fetch_remotive(keywords: frozenset[str]) -> list[dict]:
    """
    Obtiene ofertas de Remotive API consultando múltiples categorías
    (software-development, qa, data) para mayor cobertura.

    Estructura del payload por categoría:
      { "jobs": [ {...job1...}, {...job2...} ], ... }

    Campos relevantes:
      - title        : título del puesto
      - company_name : nombre de la empresa
      - url          : URL directa a la oferta
    """
    jobs: list[dict] = []
    now = _now_iso()

    for category in _REMOTIVE_CATEGORIES:
        api_url = _REMOTIVE_BASE.format(category=category)
        try:
            resp = requests.get(api_url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Remotive %s — error: %s", category, exc)
            continue

        for item in resp.json().get("jobs", []):
            if not isinstance(item, dict):
                continue

            title   = (item.get("title")        or "").strip()
            job_url = (item.get("url")           or "").strip()

            if not title or not job_url:
                continue

            if not _title_matches(title, keywords):
                continue

            jobs.append({
                "title":   title,
                "company": (item.get("company_name") or "").strip(),
                "job_url": job_url,
                "sent_at": now,
            })

    return jobs


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

# Registro de fuentes: (nombre_para_logs, función_fetcher)
_SOURCES = [
    ("RemoteOK", _fetch_remoteok),
    ("Remotive", _fetch_remotive),
]


def fetch_api_jobs(
    tech_keywords: set[str] | None = None,
) -> list[dict]:
    """
    Recolecta y normaliza ofertas de trabajo desde APIs públicas.

    Por defecto filtra títulos que contengan: QA, Automation, Tester,
    Testing, Python o React. Pasar `tech_keywords` para sobrescribir.

    Cada fuente se consulta en un bloque try/except independiente:
    si RemoteOK falla, Remotive sigue ejecutándose y viceversa.

    Parámetros:
        tech_keywords -- set de strings (case-insensitive) para filtrar
                         títulos. None usa los defaults del módulo.

    Retorna:
        Lista deduplicada de dicts con esquema:
        {'title': str, 'company': str, 'job_url': str, 'sent_at': str}
    """
    # Normalizar keywords a minúsculas para comparación uniforme
    if tech_keywords is None:
        keywords = _DEFAULT_KEYWORDS
    else:
        keywords = frozenset(kw.lower() for kw in tech_keywords)

    all_jobs: list[dict] = []

    for source_name, fetcher in _SOURCES:
        try:
            batch = fetcher(keywords)
            log.info("%s — %d oferta(s) encontrada(s).", source_name, len(batch))
            all_jobs.extend(batch)
        except requests.HTTPError as exc:
            log.warning(
                "%s — HTTP %s al consultar la API. Continuando con otras fuentes.",
                source_name, exc.response.status_code,
            )
        except requests.RequestException as exc:
            log.warning(
                "%s — Error de red: %s. Continuando con otras fuentes.",
                source_name, exc,
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.warning(
                "%s — Estructura JSON inesperada: %s. Continuando con otras fuentes.",
                source_name, exc,
            )

    # Deduplicar por job_url (puede haber overlap entre fuentes)
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for job in all_jobs:
        url = job.get("job_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(job)

    log.info("fetch_api_jobs — %d oferta(s) únicas en total.", len(unique))
    return unique
