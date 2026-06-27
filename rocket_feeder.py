"""
rocket_feeder.py
----------------
Extractor liviano de ofertas frescas desde Remote Rocketship.

Lee la página server-rendered de Remote Rocketship con headers de navegador,
parsea el JSON interno de Next.js cuando está disponible y normaliza cada oferta
al schema interno:
  {'title', 'company', 'job_url', 'location'}
"""
import html
import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse

import requests


log = logging.getLogger("rocket_feeder")

ROCKETSHIP_URL = "https://www.remoterocketship.com/"

ROCKETSHIP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.remoterocketship.com/",
}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _clean_text(value: object) -> str:
    """Normaliza texto proveniente de JSON/HTML."""
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _canonical_url(raw_url: str | None) -> str:
    """Elimina query params/fragments y resuelve wrappers internos si existen."""
    if not raw_url:
        return ""

    candidate = html.unescape(str(raw_url).strip())
    if not candidate:
        return ""

    absolute = urljoin(ROCKETSHIP_URL, candidate)
    parsed = urlparse(absolute)

    # Remote Rocketship a veces podría envolver URLs externas en query params.
    if parsed.netloc.endswith("remoterocketship.com") and parsed.query:
        query = parse_qs(parsed.query)
        for key in ("url", "u", "target", "redirect", "redirect_url", "externalUrl"):
            wrapped = query.get(key, [None])[0]
            if wrapped:
                return _canonical_url(wrapped)

    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _company_from_url(job_url: str) -> str:
    """Extrae un nombre aproximado de empresa desde URLs internas como /company/acme/jobs/..."""
    try:
        segments = [seg for seg in urlparse(job_url).path.split("/") if seg]
        if "company" in segments:
            idx = segments.index("company")
            if len(segments) > idx + 1:
                return segments[idx + 1].replace("-", " ").replace("_", " ").title()
    except Exception:
        pass
    return "Remote Rocketship"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _extract_next_data(html_text: str) -> dict | None:
    """Extrae y parsea el script __NEXT_DATA__ si existe."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None

    try:
        return json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        log.warning("Remote Rocketship: no se pudo parsear __NEXT_DATA__: %s", exc)
        return None


def _walk_json(value: object):
    """Generador DFS para recorrer dict/list arbitrarios."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _as_company_name(value: object) -> str:
    """Obtiene company name desde string o dict anidado."""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        for key in ("name", "companyName", "title", "slug"):
            text = _clean_text(value.get(key))
            if text:
                return text.replace("-", " ").title() if key == "slug" else text
    return ""


def _job_from_json_node(node: dict) -> dict | None:
    """Mapea un nodo JSON de Remote Rocketship al schema interno si parece job."""
    title = _clean_text(
        node.get("roleTitle")
        or node.get("title")
        or node.get("jobTitle")
        or node.get("categorizedJobTitle")
    )
    raw_url = node.get("url") or node.get("applyUrl") or node.get("jobUrl")
    job_url = _canonical_url(raw_url)

    if not title or not job_url:
        return None

    company = _clean_text(
        node.get("companyName")
        or node.get("organizationName")
        or node.get("hiringOrganizationName")
    )
    if not company:
        company = _as_company_name(node.get("company") or node.get("hiringOrganization"))
    if not company:
        company = _company_from_url(job_url)

    location = _clean_text(
        node.get("location")
        or node.get("jobLocation")
        or node.get("locationName")
        or node.get("country")
        or node.get("locationType")
        or "Remote"
    )

    return {
        "title": title,
        "company": company,
        "job_url": job_url,
        "location": location,
    }


def _jobs_from_next_data(html_text: str) -> list[dict]:
    """Extrae jobs desde __NEXT_DATA__ recorriendo nodos con roleTitle/url."""
    data = _extract_next_data(html_text)
    if not data:
        return []

    jobs: list[dict] = []
    seen_urls: set[str] = set()
    for node in _walk_json(data):
        job = _job_from_json_node(node)
        if not job or job["job_url"] in seen_urls:
            continue
        seen_urls.add(job["job_url"])
        jobs.append(job)

    return jobs


# ---------------------------------------------------------------------------
# HTML fallback
# ---------------------------------------------------------------------------

class _RocketshipLinkParser(HTMLParser):
    """Fallback minimalista: extrae links internos a jobs desde HTML cards."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href") or ""
        if "/jobs/" in href and ("/company/" in href or "/publicjobs/" in href):
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            text = _clean_text(" ".join(self._text_parts))
            if text:
                self.links.append((self._href, text))
            self._href = None
            self._text_parts = []


def _jobs_from_html_links(html_text: str) -> list[dict]:
    """Fallback si Remote Rocketship cambia __NEXT_DATA__ pero deja links SSR."""
    parser = _RocketshipLinkParser()
    parser.feed(html_text)

    jobs: list[dict] = []
    seen_urls: set[str] = set()
    for href, title in parser.links:
        job_url = _canonical_url(href)
        if not job_url or job_url in seen_urls:
            continue
        seen_urls.add(job_url)
        jobs.append(
            {
                "title": title,
                "company": _company_from_url(job_url),
                "job_url": job_url,
                "location": "Remote",
            }
        )

    return jobs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_rocketship_jobs(query: str = "", page: int = 1, limit: int = 80) -> list[dict]:
    """
    Descarga ofertas frescas de Remote Rocketship y las normaliza.

    Usa la página de resultados ordenada por fecha como endpoint SSR:
      https://www.remoterocketship.com/?page=1&sort=DateAdded

    Si query no está vacío, agrega el parámetro q para intentar acotar resultados.
    """
    params = {
        "page": str(max(1, page)),
        "sort": "DateAdded",
    }
    if query:
        params["q"] = query

    session = requests.Session()
    session.headers.update(ROCKETSHIP_HEADERS)

    response = session.get(ROCKETSHIP_URL, params=params, timeout=20)
    response.raise_for_status()

    html_text = response.text
    jobs = _jobs_from_next_data(html_text)
    if not jobs:
        jobs = _jobs_from_html_links(html_text)

    if limit > 0:
        jobs = jobs[:limit]

    log.info("Remote Rocketship: %d oferta(s) normalizada(s).", len(jobs))
    return jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for job in fetch_rocketship_jobs(limit=10):
        print(job)
