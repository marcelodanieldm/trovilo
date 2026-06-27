"""
search_scraper.py
-----------------
Raspa ofertas de trabajo desde DuckDuckGo y Google Search simulando
comportamiento humano: tipeo caracter a caracter con jitter y scroll gradual.

A diferencia de `scraper.py` (que construye URLs directas), este módulo
navega a los buscadores, escribe la query en el campo de búsqueda y
extrae los resultados orgánicos, lo que lo hace más resistente a
cambios en la estructura de las páginas ATS.

Funciones principales:
  scrape_jobs_via_duckduckgo(tech, location, job_type)
      Usa DuckDuckGo — menos restricciones anti-bot, recomendado.

  scrape_jobs_via_google(tech, location, job_type)
      Usa Google Search — mayor cobertura, más probabilidad de CAPTCHA.

Ambas devuelven: [{'title': str, 'company': str, 'url': str}, ...]
"""
import time
import random
from urllib.parse import urlparse, parse_qs, unquote, quote_plus
from playwright.sync_api import sync_playwright, Page
from browser import get_stealth_page
from cleaners import clean_google_result, clean_and_verify_results

# Dominios ATS válidos — se usan para filtrar resultados de búsqueda
_ATS_DOMAINS = [
    "boards.greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
    "jobs.ashbyhq.com",
    "breezy.hr",
    "recruitee.com",
    "teamtailor.com",
    "homerun.co",
    "pinpointhq.com",
]

# URLs de los motores de búsqueda
# DDG Lite: versión texto puro, sin JS pesado, raramente dispara captchas
_DDG_LITE_URL = "https://duckduckgo.com/lite/?q="
_GOOGLE_URL   = "https://www.google.com"    # mayor cobertura, más restricciones


# ---------------------------------------------------------------------------
# Construcción de la query
# ---------------------------------------------------------------------------

def _build_query(tech: str, location: str, job_type: str) -> str:
    """
    Arma la query de búsqueda con operadores site: para cada plataforma ATS.
    Ejemplo: 'site:boards.greenhouse.io OR site:jobs.lever.co "Python" "Argentina" "remote"'
    """
    sites = " OR ".join(f"site:{d}" for d in _ATS_DOMAINS)
    return f'{sites} "{tech}" "{location}" "{job_type}"'


# ---------------------------------------------------------------------------
# Comportamiento humano al tipear
# ---------------------------------------------------------------------------

def _type_humanlike(page: Page, selector: str, text: str) -> None:
    """
    Escribe un texto en un campo caracter a caracter con delays aleatorios
    entre 80 ms y 220 ms para simular velocidad de tipeo humana.
    """
    campo = page.locator(selector).first
    campo.click()

    # Pausa breve antes de empezar a escribir
    time.sleep(random.uniform(0.3, 0.7))

    for char in text:
        # delay= se pasa en ms directamente a Playwright
        campo.press_sequentially(char, delay=random.uniform(80, 220))

    # Pausa natural al terminar de escribir antes de presionar Enter
    time.sleep(random.uniform(0.4, 0.9))


# ---------------------------------------------------------------------------
# Extracción y filtrado de resultados
# ---------------------------------------------------------------------------

def _resolve_ddg_redirect(href: str) -> str:
    """
    DuckDuckGo envuelve algunos enlaces en una URL de redirección del tipo:
    //duckduckgo.com/l/?uddg=https%3A%2F%2F...
    Esta función extrae la URL real si corresponde, o devuelve href sin cambios.
    """
    if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com"):
        parsed = urlparse(href if href.startswith("http") else "https:" + href)
        params = parse_qs(parsed.query)
        uddg = params.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    return href


def _resolve_google_redirect(href: str) -> str:
    """
    Google envuelve algunos enlaces orgánicos con '/url?q=https://...&sa=...'.
    Esta función extrae la URL real si corresponde, o devuelve href sin cambios.
    """
    if href.startswith("/url?"):
        parsed = urlparse("https://www.google.com" + href)
        params = parse_qs(parsed.query)
        q = params.get("q", [None])[0]
        if q:
            return unquote(q)
    return href


def _is_job_url(url: str) -> bool:
    """Devuelve True si la URL pertenece a alguna de las plataformas ATS objetivo."""
    return any(domain in url for domain in _ATS_DOMAINS)


def _extract_company(url: str) -> str:
    """
    Extrae el nombre de la empresa desde la URL del ATS.
    Patrón común a todos los ATS: https://dominio.com/empresa/...
    """
    try:
        parts = url.split("/")
        # parts: ['https:', '', 'dominio.com', 'empresa', ...]
        return parts[3].replace("-", " ").title() if len(parts) > 3 else "Desconocida"
    except Exception:
        return "Desconocida"


def _extract_results_ddg_lite(page: Page) -> list[dict]:
    """
    Motor de extracción para DuckDuckGo Lite.

    Estrategia en dos capas para cubrir variaciones de markup entre versiones:
      Capa 1 — selector canónico: <a class="result-link">
               El HTML de DDG Lite usa esta clase en todos sus resultados.
      Capa 2 — fallback por filas: recorre cada <tr> y extrae cualquier
               <a href> cuya URL apunte a un dominio ATS conocido.
               Cubre casos donde DDG cambia nombres de clase sin previo aviso.
    """
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    def _add_if_valid(href: str, raw_title: str) -> None:
        """Valida, limpia y agrega un resultado al acumulador."""
        if not href or not _is_job_url(href) or href in seen_urls:
            return
        title = raw_title.strip()
        if not title or len(title) < 4:
            return
        parsed = clean_google_result(title, href)
        seen_urls.add(href)
        jobs.append({"title": parsed["title"], "company": parsed["company"], "url": href})

    # --- Capa 1: selector canónico de DDG Lite ---
    for link in page.query_selector_all("a.result-link"):
        try:
            _add_if_valid(
                link.get_attribute("href") or "",
                link.inner_text(),
            )
        except Exception:
            continue

    # --- Capa 2: fallback por filas de tabla ---
    # Solo se activa si la capa 1 no encontró nada (por cambio de markup).
    if not jobs:
        for row in page.query_selector_all("tr"):
            try:
                for anchor in row.query_selector_all("a[href]"):
                    _add_if_valid(
                        anchor.get_attribute("href") or "",
                        anchor.inner_text(),
                    )
            except Exception:
                continue

    return clean_and_verify_results(jobs)


def _extract_results_google(page: Page) -> list[dict]:
    """
    Extrae resultados orgánicos de Google.
    Busca tarjetas 'div.g' y obtiene el <h3> como título y el <a> como URL.
    Resuelve redirecciones /url?q= de Google cuando corresponde.
    """
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    # Cada resultado orgánico de Google vive dentro de un div.g
    cards = page.query_selector_all("div.g")

    for card in cards:
        try:
            # El enlace principal del resultado contiene el <h3> con el título
            link = card.query_selector("a[href]")
            if not link:
                continue

            raw_href = link.get_attribute("href") or ""
            url = _resolve_google_redirect(raw_href)

            if not _is_job_url(url) or url in seen_urls:
                continue

            # Título desde el <h3> dentro de la tarjeta
            heading = card.query_selector("h3")
            title = heading.inner_text().strip() if heading else link.inner_text().strip()

            if not title or len(title) < 4:
                continue

            # Limpiar y separar título / empresa usando el helper de cleaners
            parsed = clean_google_result(title, url)
            seen_urls.add(url)
            jobs.append({"title": parsed["title"], "company": parsed["company"], "url": url})

        except Exception:
            continue

    return jobs


def simulate_human_behavior(page: Page) -> None:
    """
    Rompe patrones estáticos de bot moviendo el mouse a coordenadas aleatorias
    dentro del viewport y haciendo un micro-scroll aleatorio arriba/abajo.
    Llamar antes de ejecutar búsquedas o interactuar con elementos.
    """
    # Movimiento de mouse a posición aleatoria dentro del viewport (1920x1080)
    x = random.randint(100, 1820)
    y = random.randint(100, 980)
    page.mouse.move(x, y)
    time.sleep(random.uniform(0.1, 0.3))

    # Micro-scroll aleatorio hacia abajo y luego de regreso arriba
    page.evaluate("window.scrollBy(0, Math.floor(Math.random() * 200))")
    time.sleep(random.uniform(0.1, 0.25))
    page.evaluate("window.scrollBy(0, -Math.floor(Math.random() * 100))")
    time.sleep(random.uniform(0.1, 0.2))


def _scroll_gradual(page: Page) -> None:
    """Scroll gradual compartido — simula lectura humana en ambos motores."""
    page.evaluate("""
        () => new Promise((resolve) => {
            const step = 150;
            const delay = 200;
            let y = 0;
            const total = document.body.scrollHeight;
            const timer = setInterval(() => {
                window.scrollBy(0, step);
                y += step;
                if (y >= total) { clearInterval(timer); resolve(); }
            }, delay);
        })
    """)
    time.sleep(random.uniform(0.8, 1.5))


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def scrape_jobs_via_duckduckgo(
    tech: str, location: str, job_type: str
) -> list[dict]:
    """
    Busca ofertas en plataformas ATS usando DuckDuckGo Lite (HTML puro).

    Características:
      - Navega directamente a la URL de búsqueda sin interactuar con inputs.
      - Cada llamada abre y cierra un browser context fresco, eliminando
        cookies, session storage y cualquier estado de sesión anterior.
      - Jitter aleatorio de 5.2–10.7 s después de cerrar el browser,
        enfriando la IP entre batches consecutivos.

    Parámetros:
        tech      -- tecnología o stack buscado, ej. "Python"
        location  -- ciudad o país, ej. "Argentina"
        job_type  -- modalidad, ej. "remote", "hybrid", "on-site"

    Retorna:
        Lista de dicts: [{'title': str, 'company': str, 'url': str}, ...]
    """
    query = _build_query(tech, location, job_type)
    url   = f"{_DDG_LITE_URL}{quote_plus(query)}"

    results: list[dict] = []

    # sync_playwright() como context manager garantiza que el proceso del
    # browser se cierra completamente al salir del bloque, flushándose
    # cookies, localStorage y sessionStorage sin necesidad de llamadas
    # explícitas a context.clear_cookies() o storage_state().
    with sync_playwright() as pw:
        with get_stealth_page(pw) as (page, _ctx):

            # Navegación directa: sin tipeo, sin JS pesado, sin fingerprinting
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            # Movimiento de mouse y micro-scroll antes de leer el DOM
            simulate_human_behavior(page)

            results = _extract_results_ddg_lite(page)

    # El browser ya cerró — el jitter corre sin recursos de red activos.
    # random.uniform(5.2, 10.7) da una ventana amplia para evadir detección
    # de patrones de timing regulares.
    time.sleep(random.uniform(5.2, 10.7))

    return results


def scrape_jobs_via_google(
    tech: str, location: str, job_type: str
) -> list[dict]:
    """
    Busca ofertas en plataformas ATS usando Google Search.
    Simula comportamiento humano: tipeo caracter a caracter y scroll gradual.
    Nota: Google aplica restricciones más agresivas que DuckDuckGo.

    Parámetros:
        tech      -- tecnología o stack buscado, ej. "Python"
        location  -- ciudad o país, ej. "Argentina"
        job_type  -- modalidad, ej. "remote", "hybrid", "on-site"

    Retorna:
        Lista de dicts: [{'title': str, 'company': str, 'url': str}, ...]
    """
    query = _build_query(tech, location, job_type)

    with sync_playwright() as pw:
        with get_stealth_page(pw) as (page, _ctx):

            # Navegar a Google y esperar el campo de búsqueda
            # Google usa <textarea name="q"> en versiones recientes
            page.goto(_GOOGLE_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("textarea[name='q'], input[name='q']", timeout=10_000)

            # Detectar si Google usa textarea o input según la versión cargada
            input_selector = (
                "textarea[name='q']"
                if page.locator("textarea[name='q']").count() > 0
                else "input[name='q']"
            )

            # Simular comportamiento humano antes de interactuar
            simulate_human_behavior(page)

            # Escribir la query simulando tipeo humano
            _type_humanlike(page, input_selector, query)
            page.keyboard.press("Enter")

            # Esperar el contenedor de resultados orgánicos de Google
            page.wait_for_selector("#search, #rso", timeout=20_000)
            time.sleep(random.uniform(2.0, 3.5))

            _scroll_gradual(page)

            return _extract_results_google(page)

    return []
