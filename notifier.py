"""
notifier.py
-----------
Gestiona la deduplicación de ofertas y el envío de notificaciones
por Telegram usando MarkdownV2.

Responsabilidades:
  1. Cliente Supabase compartido (instanciado al importar el módulo).
  2. Verificación de duplicados contra la tabla `sent_jobs`.
  3. Inserción de nuevas ofertas en `sent_jobs`.
  4. Construcción y envío de mensajes MarkdownV2 al Bot API de Telegram.

Funciones públicas:
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
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

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
# Comprobación de duplicados
# ---------------------------------------------------------------------------

def _ya_enviado(job_url: str) -> bool:
    """
    Consulta la tabla 'sent_jobs' para verificar si la oferta ya fue enviada.
    Devuelve True si existe un registro con esa URL, False si es nueva.
    """
    resultado = (
        supabase.table("sent_jobs")
        .select("id")
        .eq("job_url", job_url)
        .execute()
    )
    return len(resultado.data) > 0


# ---------------------------------------------------------------------------
# Inserción en Supabase
# ---------------------------------------------------------------------------

def _guardar_oferta(job: dict) -> None:
    """Inserta una nueva oferta en la tabla 'sent_jobs'."""
    supabase.table("sent_jobs").insert(
        {
            "job_url": job["url"],
            "title":   job["title"],
            "company": job["company"],
        }
    ).execute()


# ---------------------------------------------------------------------------
# Notificación por Telegram
# ---------------------------------------------------------------------------

# Caracteres especiales que MarkdownV2 de Telegram exige escapar
_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def _format_usuarios(users: list[str]) -> str:
    """Formatea una lista de usuarios de Telegram como menciones escapadas."""
    return ", ".join(f"@{_escape_mdv2(u.lstrip('@'))}" for u in users)


def _escape_mdv2(text: str) -> str:
    """
    Escapa todos los caracteres especiales de MarkdownV2 de Telegram.
    Sin esto, la API devuelve 400 Bad Request si el texto tiene guiones,
    puntos, paréntesis u otros caracteres reservados.
    """
    for char in _MDV2_SPECIAL:
        text = text.replace(char, f"\\{char}")
    return text


def _escape_url(url: str) -> str:
    """
    En el segmento URL de un enlace MarkdownV2 [texto](url)
    sólo hay que escapar '\\' y ')' para no romper el parser.
    """
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _construir_mensaje(
    job: dict,
    telegram_user: str,
    tech: str,
    location: str,
) -> str:
    """
    Arma el mensaje en MarkdownV2 para enviar por Telegram.
    Usa un bloque de cita (>) para destacar los detalles de la oferta.
    Todos los valores dinámicos se escapan con _escape_mdv2.
    """
    # Normalizar usuario: sin '@' para el escape, luego reinsertamos '@'
    usuario = telegram_user.lstrip("@")

    # Escapar todos los campos de texto libre
    titulo  = _escape_mdv2(job.get("title",   "Sin título"))
    empresa = _escape_mdv2(job.get("company", "Desconocida"))
    filtro  = _escape_mdv2(f"{tech} \u2013 {location}")  # – = en-dash, menos conflictos
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
    """Arma el mensaje MarkdownV2 para notificar que no se encontraron ofertas."""
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
    Usa parse_mode MarkdownV2 y habilita la vista previa del enlace.
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

    # Lanzar excepción si el servidor de Telegram devuelve un error HTTP
    respuesta.raise_for_status()


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def notify_no_results(telegram_user: str, tech: str, location: str) -> None:
    """
    Envía un mensaje de Telegram informando que no se encontraron
    ofertas nuevas para el filtro indicado.
    """
    try:
        mensaje = _construir_mensaje_sin_resultados(telegram_user, tech, location)
        _enviar_telegram(mensaje)
        print(f"[notifier] Sin resultados notificado a '{telegram_user}'.")
    except Exception as e:
        print(f"[notifier] Error al enviar aviso sin resultados: {e}")


def process_and_notify(
    jobs: list[dict],
    users: list[str],
    tech: str = "",
    location: str = "",
) -> None:
    """
    Procesa una lista de ofertas de trabajo:
      1. Verifica si la URL ya existe en 'sent_jobs' (evita duplicados).
      2. Si es nueva, la inserta en Supabase.
      3. Envía una notificación MarkdownV2 mencionando a todos los usuarios
         cuyos filtros coincidieron con la oferta.

    Parámetros:
        jobs     -- lista de dicts con claves 'title', 'company', 'url'
        users    -- lista de usuarios de Telegram que coinciden con la oferta
        tech     -- stack tecnológico del filtro
        location -- ubicación del filtro
    """
    for job in jobs:
        job_url = job.get("url", "").strip()

        if not job_url:
            continue

        if _ya_enviado(job_url):
            print(f"[notifier] Duplicado omitido: {job_url}")
            continue

        try:
            _guardar_oferta(job)

            mensaje = _construir_mensaje(job, users, tech, location)
            _enviar_telegram(mensaje)

            nombres = ", ".join(u.lstrip("@") for u in users)
            print(f"[notifier] Notificado a {nombres}: {job['title']} — {job['company']}")

        except Exception as e:
            print(f"[notifier] Error al procesar '{job_url}': {e}")
