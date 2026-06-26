"""
notifier.py
-----------
Gestiona la deduplicación de ofertas y el envío de notificaciones
por Telegram usando MarkdownV2.

Responsabilidades:
  1. Cliente Supabase compartido (instanciado al importar el módulo).
  2. bulk_filter_and_save(jobs_list) — upsert masivo + detección de nuevas.
  3. Construcción y envío de mensajes MarkdownV2 con rate-limit de 1 msg/s.

Funciones públicas:
  bulk_filter_and_save(jobs_list)
      Upsert masivo en sent_jobs; retorna solo las ofertas realmente nuevas.

  process_and_notify(jobs, users, tech, location)
      Filtra duplicados, persiste en Supabase y notifica a todos los
      usuarios cuyos filtros coincidieron con cada oferta.

  notify_no_results(telegram_user, tech, location)
      Notifica al usuario que su búsqueda no arrojó resultados nuevos.

Variables de entorno requeridas:
  SUPABASE_URL, SUPABASE_KEY
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import time
import logging
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

log = logging.getLogger("notifier")

# ---------------------------------------------------------------------------
# Inicialización del cliente de Supabase
# ---------------------------------------------------------------------------

def _get_supabase_client() -> Client:
    """Crea y devuelve el cliente de Supabase usando las variables de entorno."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise EnvironmentError(
            "Faltan variables de entorno: SUPABASE_URL y/o SUPABASE_KEY"
        )

    return create_client(url, key)


# Cliente compartido — se instancia una sola vez al importar el módulo
supabase: Client = _get_supabase_client()


# ---------------------------------------------------------------------------
# Deduplicación y persistencia masiva
# ---------------------------------------------------------------------------

def bulk_filter_and_save(jobs_list: list[dict]) -> list[dict]:
    """
    Upsert masivo de ofertas en 'sent_jobs' y retorna solo las nuevas.

    Estrategia:
      1. Consulta qué URLs ya existen en sent_jobs (una sola query).
      2. Filtra el jobs_list para quedarse solo con las URLs nuevas.
      3. Hace un upsert en bloque sobre las nuevas (on_conflict='job_url').
      4. Retorna la lista de ofertas que realmente se insertaron por primera vez.

    Parámetros:
        jobs_list -- lista de dicts con claves 'title', 'company', 'url'
    """
    if not jobs_list:
        return []

    # Normalizar: el campo en BD es job_url pero el dict viene con 'url'
    urls = [j["url"] for j in jobs_list if j.get("url")]

    # 1. Obtener URLs ya conocidas en un solo SELECT
    try:
        existing = (
            supabase.table("sent_jobs")
            .select("job_url")
            .in_("job_url", urls)
            .execute()
        )
        known_urls = {row["job_url"] for row in (existing.data or [])}
    except Exception as e:
        log.error("Error consultando sent_jobs: %s", e)
        known_urls = set()

    # 2. Filtrar solo ofertas nuevas
    new_jobs = [j for j in jobs_list if j.get("url") and j["url"] not in known_urls]

    if not new_jobs:
        log.info("Sin ofertas nuevas para persistir.")
        return []

    # 3. Upsert masivo
    records = [
        {"job_url": j["url"], "title": j.get("title", ""), "company": j.get("company", "")}
        for j in new_jobs
    ]
    try:
        supabase.table("sent_jobs").upsert(records, on_conflict="job_url").execute()
        log.info("Upsert masivo: %d ofertas nuevas guardadas.", len(records))
    except Exception as e:
        log.error("Error en upsert masivo: %s", e)

    return new_jobs


# ---------------------------------------------------------------------------
# Comprobación individual (compatibilidad con process_and_notify legacy)
# ---------------------------------------------------------------------------

def _ya_enviado(job_url: str) -> bool:
    resultado = (
        supabase.table("sent_jobs")
        .select("id")
        .eq("job_url", job_url)
        .execute()
    )
    return len(resultado.data) > 0


# ---------------------------------------------------------------------------
# Notificación por Telegram
# ---------------------------------------------------------------------------

# Caracteres especiales que MarkdownV2 de Telegram exige escapar
_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def _format_usuarios(users: list[str]) -> str:
    """Formatea una lista de usuarios de Telegram como menciones escapadas."""
    return ", ".join(f"@{_escape_mdv2(u.lstrip('@'))}" for u in users)


def _escape_mdv2(text: str) -> str:
    for char in _MDV2_SPECIAL:
        text = text.replace(char, f"\\{char}")
    return text


def _escape_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _construir_mensaje(
    job: dict,
    telegram_user: str,
    tech: str,
    location: str,
) -> str:
    usuario = telegram_user.lstrip("@")
    titulo  = _escape_mdv2(job.get("title",   "Sin título"))
    empresa = _escape_mdv2(job.get("company", "Desconocida"))
    filtro  = _escape_mdv2(f"{tech} \u2013 {location}")
    user_e  = _escape_mdv2(usuario)
    url_e   = _escape_url(job.get("url", ""))

    return (
        "\U0001f6a8 *¡Nueva Oferta Encontrada\!*\n"
        "\n"
        f"> \U0001f4cc *Puesto:* {titulo}\n"
        f"> \U0001f3e2 *Empresa:* {empresa}\n"
        f"> \U0001f30d *Filtro:* {filtro}\n"
        f"> \U0001f464 *Responsable:* @{user_e}\n"
        f"> \U0001f517 [Ver Oferta]({url_e})"
    )


def _construir_mensaje_sin_resultados(
    telegram_user: str,
    tech: str,
    location: str,
) -> str:
    destinatario = _format_usuarios([telegram_user])
    filtro = _escape_mdv2(f"{tech} \u2013 {location}")

    return (
        f"\U0001f50d *Sin resultados para {destinatario}*\n"
        "\n"
        f"> \U0001f30d *Filtro:* {filtro}\n"
        "> No se encontraron ofertas nuevas para este criterio\."
    )


def _enviar_telegram(mensaje: str) -> None:
    """
    Envía un mensaje al chat de Telegram configurado usando el Bot API.
    Lanza excepción en caso de error HTTP para que el llamador lo maneje.
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise EnvironmentError(
            "Faltan variables de entorno: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID"
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    respuesta = requests.post(
        url,
        json={
            "chat_id":    chat_id,
            "text":       mensaje,
            "parse_mode": "MarkdownV2",
        },
        timeout=10,
    )
    respuesta.raise_for_status()


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------

def notify_no_results(telegram_user: str, tech: str, location: str) -> None:
    """Notifica al usuario que no se encontraron ofertas nuevas."""
    try:
        mensaje = _construir_mensaje_sin_resultados(telegram_user, tech, location)
        _enviar_telegram(mensaje)
        log.info("Sin resultados notificado a '%s'.", telegram_user)
    except Exception as e:
        log.error("Error al enviar aviso sin resultados: %s", e)


def process_and_notify(
    jobs: list[dict],
    users: list[str],
    tech: str = "",
    location: str = "",
) -> None:
    """
    Filtra duplicados con bulk_filter_and_save, luego envía una notificación
    por cada oferta nueva con sleep(1) entre mensajes para respetar el
    rate-limit de Telegram (máx. 30 msg/s → 1 msg/s es conservador y seguro).

    Parámetros:
        jobs     -- lista de dicts con claves 'title', 'company', 'url'
        users    -- lista de usuarios de Telegram que coinciden con la oferta
        tech     -- stack tecnológico del filtro
        location -- ubicación del filtro
    """
    # Upsert masivo: retorna solo las que no existían
    new_jobs = bulk_filter_and_save(jobs)

    if not new_jobs:
        log.info("Todas las ofertas ya habían sido enviadas.")
        return

    log.info("%d oferta(s) nuevas a notificar.", len(new_jobs))

    for job in new_jobs:
        # Notificar a cada usuario con rate-limit
        for user in users:
            try:
                mensaje = _construir_mensaje(job, user, tech, location)
                _enviar_telegram(mensaje)
                log.info("Notificado a @%s: %s — %s", user.lstrip("@"), job.get("title"), job.get("company"))
                # Rate-limit: 1 mensaje por segundo (seguro frente al límite de Telegram)
                time.sleep(1)
            except Exception as e:
                log.error("Error notificando a '%s' para '%s': %s", user, job.get("url"), e)
