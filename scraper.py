"""
scraper.py
----------
Raspa ofertas de trabajo de plataformas ATS usando búsquedas de Google
con el operador `site:` y una página Playwright en modo stealth.

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
from playwright.sync_api import Page

# Plataformas ATS objetivo — se usan como filtro site: en la búsqueda
ATS_DOMAINS = [
    "boards.greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
    "jobs.ashbyhq.com",
]


# ---------------------------------------------------------------------------
# Utilidades de comportamiento humano
# ---------------------------------------------------------------------------

def _jitter(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa aleatoria entre peticiones para evitar detección de bots."""
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)


def _scroll_page(page) -> None:
    """
    Simula un scroll humano hacia abajo en la página en pasos graduales.
    Usa requestAnimationFrame para que el movimiento sea más natural.
    """
    page.evaluate("""
        () => new Promise((resolve) => {
            const distance = 120;          // píxeles por paso
            const delayMs  = 180;          // milisegundos entre pasos
            let scrolled = 0;
            const total = document.body.scrollHeight;

            const timer = setInterval(() => {
                window.scrollBy(0, distance);
                scrolled += distance;
                if (scrolled >= total) {
                    clearInterval(timer);
                    resolve();
                }
            }, delayMs);
        })
    """)
    # Pequeña pausa tras terminar el scroll
    time.sleep(random.uniform(0.5, 1.2))


# ---------------------------------------------------------------------------
# Construcción y parseo de búsquedas
# ---------------------------------------------------------------------------

def _build_search_url(domain: str, tech: str, location: str, job_type: str) -> str:
    """
    Arma la URL de Google con el operador site: para buscar ofertas
    en el dominio ATS indicado.
    """
    query = f'site:{domain} "{tech}" "{job_type}" "{location}"'
    return f"https://www.google.com/search?q={quote_plus(query)}&num=20&hl=en"


def _extract_company(url: str) -> str:
    """
    Extrae el nombre de la empresa desde la URL del ATS.
    Todos los ATS objetivo siguen el patrón: https://dominio/empresa/...
    """
    try:
        parts = url.split("/")
        # parts: ['https:', '', 'dominio.com', 'empresa', ...]
        return parts[3].replace("-", " ").title() if len(parts) > 3 else "Desconocida"
    except Exception:
        return "Desconocida"


def _parse_google_results(page, domain: str) -> list[dict]:
    """
    Extrae los resultados de búsqueda de Google que corresponden
    al dominio ATS indicado.
    Devuelve una lista de dicts con 'title', 'company' y 'url'.
    """
    jobs = []

    # Cada resultado orgánico de Google está en un contenedor div.g
    result_cards = page.query_selector_all("div.g")

    for card in result_cards:
        try:
            title_el = card.query_selector("h3")
            link_el  = card.query_selector("a[href]")

            if not title_el or not link_el:
                continue

            url = link_el.get_attribute("href") or ""

            # Descartar resultados que no pertenezcan al dominio ATS objetivo
            if domain not in url:
                continue

            # Ignorar páginas raíz de empresas (sin ID de oferta específica)
            path_parts = url.rstrip("/").split("/")
            if len(path_parts) < 5:
                continue

            title   = title_el.inner_text().strip()
            company = _extract_company(url)

            if title and url:
                jobs.append({"title": title, "company": company, "url": url})

        except Exception:
            # Ignorar tarjetas malformadas sin interrumpir el scraping
            continue

    return jobs


# ---------------------------------------------------------------------------
# Funciones de scraping
# ---------------------------------------------------------------------------

def scrape_ats_with_page(page: Page, tech: str, location: str, job_type: str) -> list[dict]:
    """
    Versión de scrape_ats que recibe una página de Playwright ya creada.
    Permite reutilizar una sola instancia del navegador para múltiples búsquedas.

    Parámetros:
        page     -- página de Playwright activa (gestionada externamente)
        tech     -- tecnología o stack buscado, ej. "Python"
        location -- ciudad o país, ej. "Argentina" o "Remote"
        job_type -- modalidad, ej. "remote", "hybrid", "on-site"

    Retorna:
        Lista de dicts: [{'title': str, 'company': str, 'url': str}, ...]
    """
    all_jobs: list[dict] = []

    for domain in ATS_DOMAINS:
        search_url = _build_search_url(domain, tech, location, job_type)

        try:
            # Navegar a la búsqueda y esperar a que cargue el contenido
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)

            # Simular lectura y scroll humano
            _scroll_page(page)

            # Extraer resultados del dominio actual
            jobs = _parse_google_results(page, domain)
            all_jobs.extend(jobs)

        except Exception as e:
            # Registrar el error sin detener el scraping de los demás dominios
            print(f"[scraper] Error en {domain}: {e}")

        # Jitter entre búsquedas para no disparar rate-limiting
        _jitter(min_s=2.0, max_s=5.0)

    return all_jobs


def scrape_ats(tech: str, location: str, job_type: str) -> list[dict]:
    """
    Wrapper de conveniencia que crea su propio navegador stealth.
    Útil para ejecuciones únicas o pruebas.
    """
    with sync_playwright() as pw:
        with get_stealth_page(pw) as (page, _context):
            return scrape_ats_with_page(page, tech, location, job_type)
