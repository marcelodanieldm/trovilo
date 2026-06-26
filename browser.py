"""
browser.py
----------
Instancia un navegador Chromium con configuración anti-bot usando Playwright.

Expone el context manager `get_stealth_page(playwright)` que devuelve
(page, context) listos para hacer scraping sin ser detectados como bots.

Técnicas aplicadas:
  - Rotación aleatoria de User-Agent entre 3 perfiles reales de Chrome.
  - Eliminación de flags de automatización vía `ignore_default_args`.
  - Inyección de script que neutraliza `navigator.webdriver` antes de
    que cargue cualquier JS del sitio objetivo.
  - Cabeceras HTTP y locale coherentes con el perfil de escritorio.
  - `ignore_https_errors=True` para no interrumpir el scraping por
    certificados inválidos.
"""
import random
from contextlib import contextmanager
from playwright.sync_api import Playwright

# Pool de tres User-Agents reales de escritorio Chrome para rotación aleatoria
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
]

# Script inyectado en cada página antes de que corra cualquier JS del sitio.
# Elimina navigator.webdriver y simula propiedades de un navegador humano real.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
Object.defineProperty(navigator, 'languages', { get: () => ['es-419', 'es', 'en'] });
window.chrome = { runtime: {} };
"""


@contextmanager
def get_stealth_page(playwright: Playwright):
    """
    Context manager que devuelve (page, context) de Playwright configurados
    para maximizar la evasión anti-bot en Google Search y sitios ATS.

    Cambios vs versión anterior:
      - Rotación aleatoria entre 3 User-Agents reales de Chrome escritorio
      - Se eliminan --enable-automation y --disable-blink-features vía ignore_default_args
      - navigator.webdriver se neutraliza con add_init_script (más fiable que flags)
      - ignore_https_errors=True para no interrumpir por certificados inválidos
      - Accept-Language en español latinoamericano

    Uso:
        from playwright.sync_api import sync_playwright
        from browser import get_stealth_page

        with sync_playwright() as pw:
            with get_stealth_page(pw) as (page, context):
                page.goto("https://example.com")
    """
    # Seleccionar un User-Agent distinto en cada sesión
    user_agent = random.choice(_USER_AGENTS)

    # Lanzar Chromium sin los flags que identifican sesiones automatizadas.
    # ignore_default_args elimina los argumentos que Playwright agrega por defecto.
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1920,1080",
        ],
        ignore_default_args=[
            "--enable-automation",                      # desactiva el modo automatización de Chrome
            "--disable-blink-features=AutomationControlled",  # evita exponer señales de blink
        ],
    )

    # Crear contexto con perfil de escritorio realista e ignorar errores HTTPS
    context = browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1920, "height": 1080},
        locale="es-419",
        timezone_id="America/Argentina/Buenos_Aires",
        ignore_https_errors=True,  # evitar interrupciones por certificados inválidos
        extra_http_headers={
            "Accept-Language": "es-419,es;q=0.9,en;q=0.8",  # coherente con el locale
        },
    )

    # Inyectar el script de sigilo antes de que cargue cualquier página
    context.add_init_script(_STEALTH_INIT_SCRIPT)

    page = context.new_page()

    try:
        yield page, context  # devolver ambos para permitir uso avanzado del llamador
    finally:
        # Cerrar contexto y navegador aunque ocurra una excepción
        context.close()
        browser.close()
