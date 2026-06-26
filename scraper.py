"""
scraper.py
----------
Raspa ofertas de trabajo de plataformas ATS usando DuckDuckGo HTML
(html.duckduckgo.com/html/) con Playwright en modo stealth.

Plataformas objetivo:
  - boards.greenhouse.io
  - jobs.lever.co
  - apply.workable.com
  - jobs.ashbyhq.com

Funciones principales:
  scrape_ats_with_page(page, tech, location, job_type)
      Reutiliza una página Playwright ya abierta (recomendado para
      procesar múltiples filtros con un solo navegador).

  scrape_ats(tech, location, job_type)
      Wrapper de conveniencia que crea y cierra su propio navegador.
      Útil para pruebas o ejecuciones únicas.
"""
import time
import random
import re
from urllib.parse import quote_plus
from playwright.sync_api import Page

# Plataformas ATS objetivo
ATS_DOMAINS = [
    "boards.greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
    "jobs.ashbyhq.com",
]

DDG_HTML_URL = "https://html.duckduckgo.com/html/"


# ---------------------------------------------------------------------------
# Utilidades de comportamiento humano
# ---------------------------------------------------------------------------

def _jitter(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa aleatoria entre peticiones para evitar detección de bots."""
    time.sleep(random.uniform(min_s, max_s))


# ---------------------------------------------------------------------------
# Construcción y parseo de búsquedas
# ---------------------------------------------------------------------------

def _build_query(tech: str, location: str, job_type: str) -> str:
    """
    Arma la query combinando los dominios ATS con operadores OR y los
    términos entre comillas para mayor precisión.
    """
    site_filter = " OR ".join(f"site:{d}" for d in ATS_DOMAINS)
    return f'{site_filter} "{tech}" "{location}" "{job_type}"'


def _extract_company(url: str) -> str:
    """
    Extrae el nombre de la empresa desde la URL del ATS.
    Todos los ATS objetivo siguen el patrón: https://dominio/empresa/...
    """
    try:
        parts = url.split("/")
        return parts[3].replace("-", " ").title() if len(parts) > 3 else "Desconocida"
    except Exception:
        return "Desconocida"


def _clean_title(title: str) -> str:
    """
    Limpia el título removiendo sufijos de dominio, pipes y espacios extra
    que suelen agregar los motores de búsqueda.
    """
    # Quitar todo lo que venga después de " | ", " - ", " — ", " at "
    title = re.split(r"\s[\|\-\—]\s|\s+at\s+", title)[0]
    return title.strip()


def _is_valid_ats_url(url: str) -> bool:
    """Verifica que la URL pertenezca a uno de los dominios ATS objetivo."""
    return any(domain in url for domain in ATS_DOMAINS)


def _extract_results_from_page(page, seen_urls: set) -> list[dict]:
    """Extrae ofertas ATS de la página actual de DuckDuckGo HTML."""
    jobs = []
    result_links = page.query_selector_all(".result__url, .result__a")

    for el in result_links:
        try:
            tag = el.evaluate("el => el.tagName.toLowerCase()")

            if tag == "a":
                url   = el.get_attribute("href") or ""
                title = _clean_title(el.inner_text().strip())
            else:
                url   = el.inner_text().strip()
                title = ""

            if not url or not _is_valid_ats_url(url):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not title:
                parent = el.evaluate_handle("el => el.closest('.result')")
                title_el = parent.query_selector(".result__a")
                if title_el:
                    title = _clean_title(title_el.inner_text().strip())

            company = _extract_company(url)
            if title and url:
                jobs.append({"title": title, "company": company, "url": url})

        except Exception:
            continue

    return jobs


# ---------------------------------------------------------------------------
# Scraping via DuckDuckGo HTML
# ---------------------------------------------------------------------------

def scrape_jobs_via_duckduckgo(
    page: Page,
    tech: str,
    location: str,
    job_type: str,
) -> list[dict]:
    """
    Busca ofertas en DuckDuckGo HTML (sin JS, más liviano y estable).

    Estrategia:
      1. Navega a html.duckduckgo.com/html/
      2. Llena el formulario de búsqueda con la query construida
      3. Extrae resultados orgánicos de la página 1
      4. Si existe el botón "Next" (.nav-link form), lo pulsa y extrae página 2
      5. Filtra solo URLs de dominios ATS y retorna lista de dicts
    """
    query = _build_query(tech, location, job_type)
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    try:
        page.goto(DDG_HTML_URL, wait_until="domcontentloaded", timeout=30_000)
        _jitter(1.0, 2.5)

        search_input = page.query_selector("input[name='q']")
        if not search_input:
            print("[scraper] No se encontró el campo de búsqueda de DuckDuckGo.")
            return jobs

        search_input.fill(query)
        _jitter(0.5, 1.2)
        search_input.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
        _jitter(1.0, 2.0)

        # Página 1
        jobs.extend(_extract_results_from_page(page, seen_urls))

        # Página 2 — si existe el botón "Next"
        next_btn = page.query_selector(".nav-link form")
        if next_btn:
            try:
                next_btn.evaluate("form => form.submit()")
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
                _jitter(1.0, 2.0)
                jobs.extend(_extract_results_from_page(page, seen_urls))
            except Exception as e:
                print(f"[scraper] Error cargando página 2: {e}")

    except Exception as e:
        print(f"[scraper] Error buscando en DuckDuckGo: {e}")

    return jobs


# ---------------------------------------------------------------------------
# Funciones públicas (mantienen la misma interfaz que antes)
# ---------------------------------------------------------------------------

def scrape_ats_with_page(page: Page, tech: str, location: str, job_type: str) -> list[dict]:
    """
    Wrapper público que delega en scrape_jobs_via_duckduckgo.
    Mantiene la misma firma para no romper main.py.
    """
    # job_type puede venir como "remote,hybrid" — buscar una vez por modalidad
    types = [t.strip() for t in job_type.split(",") if t.strip()]
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for jt in types:
        results = scrape_jobs_via_duckduckgo(page, tech, location, jt)
        for job in results:
            if job["url"] not in seen:
                seen.add(job["url"])
                all_jobs.append(job)
        if len(types) > 1:
            _jitter(2.0, 4.0)

    return all_jobs


def scrape_ats(tech: str, location: str, job_type: str) -> list[dict]:
    """
    Wrapper de conveniencia que crea su propio navegador stealth.
    Útil para ejecuciones únicas o pruebas.
    """
    from playwright.sync_api import sync_playwright
    from browser import get_stealth_page

    with sync_playwright() as pw:
        with get_stealth_page(pw) as (page, _context):
            return scrape_ats_with_page(page, tech, location, job_type)
