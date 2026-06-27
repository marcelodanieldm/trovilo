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
import re
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from browser import get_stealth_page
from scraper import execute_unbreakable_scraping
from notifier import supabase, bulk_filter_and_save, process_and_notify, notify_no_results
from ats_engine import fetch_greenhouse_jobs, fetch_lever_jobs, RateLimitError
from telegram_notifier import send_job_alert

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
# Filtro de palabras clave para el pipeline ATS
# ---------------------------------------------------------------------------

# Solo se notifican y persisten ofertas cuyo título contenga alguno de estos términos.
# Ajustar según los intereses del equipo.
_TITLE_KEYWORDS = re.compile(
    r'\b(qa|quality[\s\-]*assurance|automation|python|react|node\.?js|remote)\b',
    re.IGNORECASE,
)


def _matches_keywords(job: dict) -> bool:
    """Retorna True si el título del job contiene al menos una keyword relevante."""
    return bool(_TITLE_KEYWORDS.search(job.get("title", "")))


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
    Soporta tanto el schema v1 (columna 'active') como v2 (columna 'is_active').
    """
    resultado = (
        supabase.table("target_companies")
        .select("company_name, ats_type, ats_id")
        .eq("is_active", True)
        .execute()
    )
    return resultado.data or []


# ---------------------------------------------------------------------------
# Pipeline ATS directo con Telegram
# ---------------------------------------------------------------------------

def run_ats_pipeline() -> None:
    """
    Pipeline de ingesta directa via APIs de ATS (Greenhouse / Lever).

    Flujo:
      1. Descarga target_companies activas de Supabase.
      2. Por cada empresa llama al fetcher correspondiente según ats_type.
      3. Filtra las ofertas por _TITLE_KEYWORDS (QA, Automation, Python…).
      4. Consulta sent_jobs para identificar las realmente nuevas.
      5. Envía un alert de Telegram por cada oferta nueva.
      6. Hace upsert masivo en sent_jobs con title, company, job_url, sent_at.
    """
    log.info("── ATS pipeline: cargando empresas objetivo...")

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

    # 1 + 2. Recolectar todas las ofertas de todos los ATS
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
            log.warning("ATS pipeline: rate-limit en %s — esperando 60 s.", name)
            time.sleep(60)
        except Exception as exc:
            log.error("ATS pipeline: error en %s (%s): %s", name, ats_type, exc)

    log.info("ATS pipeline: %d oferta(s) recolectada(s) antes del filtro.", len(all_jobs))

    # 3. Filtrar por palabras clave en el título
    relevant = [j for j in all_jobs if _matches_keywords(j)]
    log.info(
        "ATS pipeline: %d oferta(s) relevantes (keyword match).",
        len(relevant),
    )

    if not relevant:
        log.info("ATS pipeline: ninguna oferta supera el filtro de keywords.")
        return

    # 4. Consultar sent_jobs para identificar las realmente nuevas
    urls = [j["job_url"] for j in relevant if j.get("job_url")]
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

    new_jobs = [j for j in relevant if j.get("job_url") and j["job_url"] not in known]

    log.info(
        "ATS pipeline: %d oferta(s) nueva(s) para notificar.",
        len(new_jobs),
    )

    if not new_jobs:
        log.info("ATS pipeline: sin ofertas nuevas. Nada que notificar.")
        return

    # 5. Enviar alert de Telegram por cada oferta nueva
    sent_count = 0
    for job in new_jobs:
        ok = send_job_alert(
            title    = job.get("title",    ""),
            company  = job.get("company",  ""),
            location = job.get("location", ""),
            job_url  = job["job_url"],
        )
        if ok:
            sent_count += 1
        # Rate limit: máx ~2 mensajes/s para no saturar la API de Telegram
        time.sleep(0.5)

    log.info("ATS pipeline: %d/%d alertas de Telegram enviadas.", sent_count, len(new_jobs))

    # 6. Upsert masivo en sent_jobs
    now_iso = datetime.now(timezone.utc).isoformat()
    records = [
        {
            "job_url":  j["job_url"],
            "title":    j.get("title",   ""),
            "company":  j.get("company", ""),
            "sent_at":  now_iso,
        }
        for j in new_jobs
    ]

    try:
        supabase.table("sent_jobs").upsert(records, on_conflict="job_url").execute()
        log.info(
            "ATS pipeline: upsert de %d oferta(s) en sent_jobs completado.",
            len(records),
        )
    except Exception as exc:
        log.error("ATS pipeline: error en upsert de sent_jobs: %s", exc)


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
