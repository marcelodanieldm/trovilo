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
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from browser import get_stealth_page
from scraper import execute_unbreakable_scraping
from notifier import supabase, bulk_filter_and_save, process_and_notify, notify_no_results
from ats_engine import fetch_greenhouse_jobs, fetch_lever_jobs, RateLimitError

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


def fetch_target_companies() -> list[dict]:
    """
    Descarga las empresas activas de 'target_companies'.
    Retorna lista de dicts con claves: company_name, ats_type, ats_id.
    """
    resultado = (
        supabase.table("target_companies")
        .select("company_name, ats_type, ats_id")
        .eq("active", True)
        .execute()
    )
    return resultado.data or []


# ---------------------------------------------------------------------------
# Pipeline ATS directo
# ---------------------------------------------------------------------------

def run_ats_pipeline() -> None:
    """
    Pipeline de ingesta directa via APIs de ATS (Greenhouse / Lever).

    Flujo:
      1. Descarga target_companies activas de Supabase.
      2. Por cada empresa llama al fetcher correspondiente según ats_type.
      3. Agrega todos los jobs en un único array y hace upsert masivo
         en sent_jobs (deduplicación por job_url).
    """
    log.info("-- ATS pipeline: cargando empresas objetivo...")

    try:
        companies = fetch_target_companies()
    except Exception as exc:
        log.error("ATS pipeline: error al cargar target_companies: %s", exc)
        return

    if not companies:
        log.warning("ATS pipeline: sin empresas activas en target_companies.")
        return

    log.info("ATS pipeline: %d empresa(s) activa(s).", len(companies))

    _FETCHERS = {
        "greenhouse": fetch_greenhouse_jobs,
        "lever":      fetch_lever_jobs,
    }

    all_jobs: list[dict] = []

    for row in companies:
        ats_type = (row.get("ats_type") or "").lower()
        ats_id   =  row.get("ats_id")   or ""
        name     =  row.get("company_name") or ats_id

        fetcher = _FETCHERS.get(ats_type)
        if not fetcher:
            log.warning("ATS pipeline: ats_type '%s' desconocido (%s).", ats_type, name)
            continue

        try:
            jobs = fetcher(ats_id)
            all_jobs.extend(jobs)
        except RateLimitError:
            log.warning("ATS pipeline: rate-limit en %s (%s) — esperando 60 s.", name, ats_type)
            time.sleep(60)
        except Exception as exc:
            log.error("ATS pipeline: error en %s (%s): %s", name, ats_type, exc)

    log.info("ATS pipeline: %d oferta(s) recolectada(s) en total.", len(all_jobs))

    if not all_jobs:
        return

    # Upsert masivo en sent_jobs.
    # ats_engine usa 'job_url' directamente (coincide con la columna de BD);
    # solo se insertan filas cuya job_url no exista aún.
    urls = [j["job_url"] for j in all_jobs if j.get("job_url")]
    try:
        existing = (
            supabase.table("sent_jobs")
            .select("job_url")
            .in_("job_url", urls)
            .execute()
        )
        known = {r["job_url"] for r in (existing.data or [])}
    except Exception as exc:
        log.error("ATS pipeline: error consultando sent_jobs: %s", exc)
        known = set()

    new_jobs = [j for j in all_jobs if j.get("job_url") and j["job_url"] not in known]

    if not new_jobs:
        log.info("ATS pipeline: sin ofertas nuevas para persistir.")
        return

    records = [
        {
            "job_url": j["job_url"],
            "title":   j.get("title",   ""),
            "company": j.get("company", ""),
        }
        for j in new_jobs
    ]

    try:
        supabase.table("sent_jobs").upsert(records, on_conflict="job_url").execute()
        log.info("ATS pipeline: upsert de %d oferta(s) nuevas completado.", len(records))
    except Exception as exc:
        log.error("ATS pipeline: error en upsert: %s", exc)


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

    # 0. Pipeline directo ATS (sin browser, sin buscadores)
    run_ats_pipeline()

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
                jobs = execute_unbreakable_scraping(page, tech, location, job_type)
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
