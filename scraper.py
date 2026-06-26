"""
scraper.py
----------
Raspa ofertas de trabajo de plataformas ATS y job boards usando
DuckDuckGo HTML con Playwright en modo stealth.

Funciones principales:
  run_massive_scraping(page, tech, location, job_type)
      Itera sobre todos los lotes de dominios generados por query_builder,
      con logging y sleep anti-detección entre batches.

  scrape_ats_with_page(page, tech, location, job_type)
      Alias público que delega en run_massive_scraping.

  scrape_ats(tech, location, job_type)
      Wrapper de conveniencia que crea su propio navegador stealth.
"""
import time
import random
import re
import logging
from urllib.parse import urlparse
from playwright.sync_api import Page
from query_builder import build_duckduckgo_query, generate_query_batches

# Logger del módulo — emite a stdout para que GitHub Actions lo capture
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# Plataformas ATS objetivo (para validación de URLs)
ATS_DOMAINS = [
    "greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "breezy.hr",
    "recruitee.com",
    "bamboohr.com",
    "jobs.smartrecruiters.com",
    "icims.com",
    "taleo.net",
    "myworkdayjobs.com",
    "jobvite.com",
    "wellfound.com",
    "weworkremotely.com",
    "remoteok.com",
    "getonbrd.com",
    "web3.career",
    "larajobs.com",
]

DDG_HTML_URL = "https://html.duckduckgo.com/html/"


# ---------------------------------------------------------------------------
# Utilidades de comportamiento humano
# ---------------------------------------------------------------------------

def _jitter(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa aleatoria entre peticiones para evitar detección de bots."""
    time.sleep(random.uniform(min_s, max_s))


# Prefijos de subdominio genéricos que no son nombres de empresa
_GENERIC_SUBDOMAINS = {"www", "jobs", "boards", "apply", "careers", "work", "hire"}


def _extract_company(url: str) -> str:
    """
    Extrae el slug de empresa desde la URL del ATS sin hardcodear cada plataforma.

    Estrategia (en orden de prioridad):
      1. Si el subdominio más a la izquierda NO es genérico (www, jobs, apply…),
         es el slug de empresa → ej. 'acme.breezy.hr' → 'Acme'
      2. Si el subdominio es genérico, el primer segmento de path es el slug
         → ej. 'apply.workable.com/acme-corp/...' → 'Acme Corp'
    """
    try:
        parsed   = urlparse(url if url.startswith("http") else f"https://{url}")
        hostname = parsed.netloc.lower()
        # Subdominio más a la izquierda (antes del primer punto)
        leftmost = hostname.split(".")[0]

        if leftmost not in _GENERIC_SUBDOMAINS:
            slug = leftmost
        else:
            # Primer segmento no vacío del path
            segments = [s for s in parsed.path.split("/") if s]
            slug = segments[0] if segments else ""

        return slug.replace("-", " ").replace("_", " ").title() if slug else "Desconocida"

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


def is_valid_job_url(url: str) -> bool:
    """
    Valida que una URL sea un posting real de un ATS objetivo.

    Reglas:
      1. El dominio debe ser exactamente de greenhouse, lever o ashby.
      2. No debe contener sub-páginas de privacidad, términos o ayuda.
      3. Para lever.co, debe tener al menos 2 segmentos de path
         (company + job-id) para no incluir páginas raíz de empresa.
    """
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        hostname = parsed.netloc.lower()
        path     = parsed.path.lower()

        # Regla 1: dominio debe contener uno de los ATS conocidos
        valid_domains = (
            "greenhouse.io",
            "jobs.lever.co",
            "jobs.ashbyhq.com",
            "apply.workable.com",
            "breezy.hr",
            "recruitee.com",
            "bamboohr.com",
            "jobs.smartrecruiters.com",
        )
        if not any(d in hostname for d in valid_domains):
            return False

        # Regla 2: descartar páginas de privacidad, términos y ayuda
        blocked_paths = ("/privacy", "/terms", "/help", "/about", "/blog")
        if any(path.startswith(p) for p in blocked_paths):
            return False

        # Regla 3: lever.co debe tener al menos company + job-id
        if "lever.co" in hostname:
            segments = [s for s in path.split("/") if s]
            if len(segments) < 2:
                return False

        return True

    except Exception:
        return False


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

            if not url or not is_valid_job_url(url):
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
# Scraping masivo via DuckDuckGo HTML
# ---------------------------------------------------------------------------

def run_massive_scraping(
    page: Page,
    tech: str,
    location: str,
    job_type: str,
) -> list[dict]:
    """
    Scraping masivo iterando sobre todos los lotes de dominios de query_builder.

    Por cada batch:
      1. Navega a html.duckduckgo.com/html/ y ejecuta la búsqueda
      2. Extrae resultados de página 1
      3. Si existe botón "Next" (.nav-link form), extrae página 2
      4. Duerme 3-6 segundos entre lotes (firma humana de baja intensidad)
      5. Si el batch falla (timeout, selector ausente), loguea y continúa
    """
    batches   = generate_query_batches(tech, location, job_type)
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    total = len(batches)

    log.info("Iniciando scraping masivo — %d lotes para tech='%s' location='%s' job_type='%s'",
             total, tech, location, job_type)

    for idx, query in enumerate(batches, start=1):
        log.info("Batch %d/%d — %d chars", idx, total, len(query))

        try:
            page.goto(DDG_HTML_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(1.0, 2.0))

            search_input = page.query_selector("input[name='q']")
            if not search_input:
                log.warning("Batch %d: campo de búsqueda no encontrado, omitiendo.", idx)
                continue

            search_input.fill(query)
            time.sleep(random.uniform(0.4, 1.0))
            search_input.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            time.sleep(random.uniform(1.0, 2.0))

            # Página 1
            before = len(jobs)
            jobs.extend(_extract_results_from_page(page, seen_urls))
            log.info("Batch %d — página 1: %d ofertas nuevas", idx, len(jobs) - before)

            # Página 2
            next_btn = page.query_selector(".nav-link form")
            if next_btn:
                try:
                    next_btn.evaluate("form => form.submit()")
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    time.sleep(random.uniform(1.0, 2.0))
                    before2 = len(jobs)
                    jobs.extend(_extract_results_from_page(page, seen_urls))
                    log.info("Batch %d — página 2: %d ofertas nuevas", idx, len(jobs) - before2)
                except Exception as e:
                    log.error("Batch %d: error en página 2 — %s", idx, e)

        except Exception as e:
            log.error("Batch %d falló — %s: %s", idx, type(e).__name__, e)

        # Sleep anti-detección entre lotes (3-6 s)
        if idx < total:
            sleep_s = random.uniform(3.0, 6.0)
            log.info("Batch %d completado. Durmiendo %.1fs antes del siguiente.", idx, sleep_s)
            time.sleep(sleep_s)

    log.info("Scraping masivo finalizado — %d ofertas únicas encontradas.", len(jobs))
    return jobs



# ---------------------------------------------------------------------------
# Funciones públicas (mantienen la misma interfaz que antes)
# ---------------------------------------------------------------------------

def scrape_ats_with_page(page: Page, tech: str, location: str, job_type: str) -> list[dict]:
    """
    Wrapper público que delega en run_massive_scraping.
    Mantiene la misma firma para no romper main.py.
    Si job_type tiene múltiples valores (ej. "remote,hybrid"),
    ejecuta un scraping masivo por cada modalidad.
    """
    types = [t.strip() for t in job_type.split(",") if t.strip()]
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for jt in types:
        results = run_massive_scraping(page, tech, location, jt)
        for job in results:
            if job["url"] not in seen:
                seen.add(job["url"])
                all_jobs.append(job)
        if len(types) > 1:
            sleep_s = random.uniform(3.0, 6.0)
            log.info("Modalidad '%s' completada. Durmiendo %.1fs antes de la siguiente.", jt, sleep_s)
            time.sleep(sleep_s)

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
