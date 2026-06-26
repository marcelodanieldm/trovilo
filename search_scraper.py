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

# Dominios ATS válidos — se usan para filtrar resultados de búsqueda
_ATS_DOMAINS = [
    "boards.greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
    "jobs.ashbyhq.com",
]

# URLs de los motores de búsqueda
_DDG_URL    = "https://duckduckgo.com"       # más permisivo para scraping
_GOOGLE_URL = "https://www.google.com"      # mayor cobertura, más restricciones


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


def _extract_results_ddg(page: Page) -> list[dict]:
    """
    Extrae resultados de DuckDuckGo.
    Recorre todos los <a href> y filtra los que apunten a dominios ATS.
    """
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    for link in page.query_selector_all("a[href]"):
        try:
            raw_href = link.get_attribute("href") or ""
            url = _resolve_ddg_redirect(raw_href)

            if not _is_job_url(url) or url in seen_urls:
                continue

            title = link.inner_text().strip()
            if not title:
                heading = link.query_selector("h2, h3")
                if heading:
                    title = heading.inner_text().strip()

            if not title or len(title) < 4:
                continue

            # Limpiar y separar título / empresa usando el helper de cleaners
            parsed  = clean_google_result(title, url)
            seen_urls.add(url)
            jobs.append({"title": parsed["title"], "company": parsed["company"], "url": url})

        except Exception:
            continue

    return jobs


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
    Busca ofertas en plataformas ATS usando DuckDuckGo.
    Simula comportamiento humano: tipeo caracter a caracter y scroll gradual.

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

            # Navegar a DuckDuckGo y esperar el campo de búsqueda
            page.goto(_DDG_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("input[name='q']", timeout=10_000)

            # Escribir la query simulando tipeo humano
            _type_humanlike(page, "input[name='q']", query)
            page.keyboard.press("Enter")

            # Esperar resultados y renderizado JS
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            time.sleep(random.uniform(2.0, 3.5))

            _scroll_gradual(page)

            return _extract_results_ddg(page)

    return []


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

            # Escribir la query simulando tipeo humano
            _type_humanlike(page, input_selector, query)
            page.keyboard.press("Enter")

            # Esperar el contenedor de resultados orgánicos de Google
            page.wait_for_selector("#search, #rso", timeout=20_000)
            time.sleep(random.uniform(2.0, 3.5))

            _scroll_gradual(page)

            return _extract_results_google(page)

    return []
