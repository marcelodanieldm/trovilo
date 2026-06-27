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

# Pool de User-Agents reales de escritorio Chrome — versiones actuales (jun 2026)
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
]

# Script inyectado en cada página ANTES de que corra cualquier JS del sitio.
# delete navigator.webdriver elimina la propiedad del prototipo para que retorne
# undefined (más robusto que Object.defineProperty, evita detección por redefinición).
_STEALTH_INIT_SCRIPT = """
delete navigator.webdriver;
Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
Object.defineProperty(navigator, 'languages', { get: () => ['es-419', 'es', 'en'] });
window.chrome = { runtime: {} };
"""


def get_organic_headers() -> dict[str, str]:
    """
    Devuelve un diccionario de cabeceras HTTP que imitan exactamente las que
    envía un Chrome 137 real en Windows 11 al navegar a una página web.

    Incluye cabeceras Client Hints (Sec-Ch-Ua-*) y Sec-Fetch-* que los
    firewalls anti-bot verifican para confirmar que la petición proviene
    de un navegador legítimo y no de una herramienta de scraping.

    Retorna:
        dict con todas las cabeceras listas para pasar a
        browser_context.new_context(extra_http_headers=...)
    """
    return {
        # Tipos de contenido aceptados — idéntico al orden que usa Chrome 137
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;"
            "q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        # Codificaciones soportadas — zstd es exclusivo de Chrome 103+
        "Accept-Encoding": "gzip, deflate, br, zstd",
        # Idioma preferido — español latinoamericano coherente con el locale
        "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
        # Client Hints de marca — tokens exactos de Chrome 137 Chromium
        "Sec-Ch-Ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",          # escritorio (no móvil)
        "Sec-Ch-Ua-Platform": '"Windows"',  # plataforma Windows
        # Metadatos de navegación — indica carga de documento top-level
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",           # navegación directa (sin referrer)
        # Redirección automática a HTTPS cuando el sitio lo soporta
        "Upgrade-Insecure-Requests": "1",
    }


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

    # Lanzar el Chromium bundled de Playwright.
    # NOTA: --disable-blink-features=AutomationControlled lo agrega Playwright
    # por defecto — no se repite aquí para evitar que Chrome rechace el
    # argumento duplicado y cierre el contexto antes de new_page().
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1920,1080",
        ],
        ignore_default_args=["--enable-automation"],
    )

    # Crear contexto con perfil de escritorio realista e ignorar errores HTTPS.
    # get_organic_headers() provee cabeceras Client Hints y Sec-Fetch-* exactas
    # de Chrome 137 en Windows 11, haciendo cada petición indistinguible de
    # un navegador real para los firewalls anti-bot.
    context = browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1920, "height": 1080},
        locale="es-419",
        timezone_id="America/Argentina/Buenos_Aires",
        ignore_https_errors=True,
        extra_http_headers=get_organic_headers(),
    )

    # Inyectar a nivel de contexto (todas las páginas y frames)
    context.add_init_script(_STEALTH_INIT_SCRIPT)

    page = context.new_page()

    # Inyectar también a nivel de página para asegurar ejecución en el frame principal
    page.add_init_script(_STEALTH_INIT_SCRIPT)

    try:
        yield page, context  # devolver ambos para permitir uso avanzado del llamador
    finally:
        # Cerrar contexto y navegador aunque ocurra una excepción
        context.close()
        browser.close()
