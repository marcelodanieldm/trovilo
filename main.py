"""
main.py
-------
Orquestador principal de trovilo — scraper masivo de ofertas de trabajo.

Flujo de ejecución:
  1. Carga variables de entorno desde `.env`.
  2. Configura logging con timestamps limpios.
  3. Descarga los filtros activos desde Supabase (`search_filters`).
  4. Abre una única instancia del navegador stealth (Playwright + Chromium).
  5. Por cada filtro activo:
       a. Ejecuta run_massive_scraping() sobre todos los lotes de dominios.
       b. Llama a bulk_filter_and_save() para deduplicar y persistir en Supabase.
       c. Dispara alertas de Telegram con rate-limit de 1 msg/s.
  6. Cierra el navegador en un bloque `finally`, incluso si ocurre un error.

Uso:
    python main.py

Programado vía GitHub Actions (ver .github/workflows/scraper.yml).
"""
import logging
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from browser import get_stealth_page
from scraper import run_massive_scraping
from notifier import supabase, bulk_filter_and_save, process_and_notify, notify_no_results

# ---------------------------------------------------------------------------
# Configuración global de logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Carga de variables de entorno
# ---------------------------------------------------------------------------

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_search_filters() -> list[dict]:
    """
    Descarga todos los filtros activos de la tabla 'search_filters' en Supabase.
    Retorna una lista de dicts con los parámetros de búsqueda de cada usuario.
    """
    resultado = supabase.table("search_filters").select("*").execute()
    return resultado.data or []


# ---------------------------------------------------------------------------
# Orquestador principal
# ---------------------------------------------------------------------------

def run() -> None:
    """
    Flujo principal:
      1. Descarga filtros de Supabase.
      2. Abre browser stealth (única instancia).
      3. Por cada filtro: scraping masivo → deduplicación → notificaciones.
      4. Cierra el browser en finally.
    """
    log.info("══════════════════════════════════════")
    log.info("Iniciando trovilo — scraper masivo")
    log.info("══════════════════════════════════════")

    # 1. Obtener filtros activos
    try:
        filtros = fetch_search_filters()
    except Exception as e:
        log.error("Error al obtener filtros de Supabase: %s", e)
        return

    if not filtros:
        log.warning("No se encontraron filtros de búsqueda activos. Saliendo.")
        return

    log.info("%d filtro(s) activo(s) encontrado(s).", len(filtros))

    # 2. Inicializar browser stealth (una sola instancia para todos los filtros)
    pw_ctx      = sync_playwright().start()
    page        = None
    context_obj = None

    try:
        context_mgr  = get_stealth_page(pw_ctx)
        page, context_obj = context_mgr.__enter__()
        log.info("Browser stealth iniciado correctamente.")

        # 3. Procesar cada filtro
        for idx, filtro in enumerate(filtros, start=1):
            tech          = filtro.get("tech_stack",    "").strip()
            location      = filtro.get("location",      "").strip()
            job_type      = filtro.get("job_type",      "").strip()
            telegram_user = filtro.get("telegram_user", "").strip()

            if not all([tech, location, job_type, telegram_user]):
                log.warning("Filtro %d incompleto, omitiendo: %s", idx, filtro)
                continue

            log.info(
                "── Filtro %d/%d — tech: '%s' | location: '%s' | "
                "job_type: '%s' | usuario: '%s'",
                idx, len(filtros), tech, location, job_type, telegram_user,
            )

            # a. Scraping masivo sobre todos los lotes de dominios
            try:
                jobs = run_massive_scraping(page, tech, location, job_type)
                log.info("Filtro %d — %d oferta(s) encontrada(s) en total.", idx, len(jobs))
            except Exception as e:
                log.error("Filtro %d — error durante el scraping: %s", idx, e)
                continue

            if not jobs:
                notify_no_results(telegram_user, tech, location)
                continue

            # b. Deduplicar, persistir en Supabase y disparar alertas
            try:
                process_and_notify(
                    jobs,
                    users=[telegram_user],
                    tech=tech,
                    location=location,
                )
            except Exception as e:
                log.error("Filtro %d — error al notificar: %s", idx, e)

        log.info("══════════════════════════════════════")
        log.info("Scraping finalizado para todos los filtros.")
        log.info("══════════════════════════════════════")

    finally:
        # 4. Garantizar cierre del browser aunque ocurra una excepción
        try:
            if context_obj:
                context_mgr.__exit__(None, None, None)
                log.info("Browser cerrado correctamente.")
        except Exception as e:
            log.warning("Error al cerrar el browser: %s", e)
        try:
            pw_ctx.stop()
        except Exception:
            pass


if __name__ == "__main__":
    run()
