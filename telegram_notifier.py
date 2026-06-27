"""
telegram_notifier.py
--------------------
Envía alertas de nuevas ofertas de trabajo al bot de Telegram usando
MarkdownV2. Lee credenciales desde variables de entorno.

Uso:
    from telegram_notifier import send_job_alert
    ok = send_job_alert(
        title    = "QA Automation Engineer",
        company  = "Vercel",
        location = "Remote",
        job_url  = "https://job-boards.greenhouse.io/vercel/jobs/123",
    )
"""
import os
import re
import logging
import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MarkdownV2 — escapado de caracteres reservados
# ---------------------------------------------------------------------------

# Todos los caracteres que Telegram requiere escapar en texto libre (fuera de
# entidades): _ * [ ] ( ) ~ ` > # + - = | { } . !
# Ver: https://core.telegram.org/bots/api#markdownv2-style
_MDV2_RE = re.compile(r'([_*\[\]()~`>#+=|{}.!\\-])')


def _escape(text: str) -> str:
    """
    Escapa caracteres reservados de MarkdownV2 en texto libre.
    Llamar siempre sobre variables externas antes de insertarlas en el mensaje.
    """
    return _MDV2_RE.sub(r'\\\1', str(text or ""))


def _escape_url(url: str) -> str:
    """
    Escapa el URL dentro del paréntesis de un inline link de MarkdownV2.
    Solo ')' y '\\' requieren escapado en esa posición.
    """
    return str(url or "").replace("\\", "\\\\").replace(")", "\\)")


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------

def send_job_alert(
    title:    str,
    company:  str,
    location: str,
    job_url:  str,
) -> bool:
    """
    Envía un mensaje estructurado de alerta al chat de Telegram.

    Variables de entorno requeridas:
        TELEGRAM_BOT_TOKEN  — token del bot (formato: 123456:ABC-DEF...)
        TELEGRAM_CHAT_ID    — ID numérico del chat / canal destino

    El campo TELEGRAM_TOKEN se acepta como alias de TELEGRAM_BOT_TOKEN
    para compatibilidad con configuraciones anteriores.

    Parámetros:
        title    — título del puesto
        company  — nombre de la empresa
        location — ubicación / modalidad (ej. "Remote", "Buenos Aires")
        job_url  — URL directa al formulario de postulación

    Retorna:
        True si la API respondió con ok=true, False en cualquier otro caso.
    """
    token   = (
        os.environ.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_TOKEN")
    )
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log.warning(
            "send_job_alert: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID no configurados."
        )
        return False

    # Construir el texto en MarkdownV2.
    # Las variables externas se pasan por _escape(); los asteriscos de negrita
    # y los emojis no se escapan porque son parte de la sintaxis del mensaje.
    text = (
        "🚀 *Nueva Oferta Encontrada*\n\n"
        f"💼 *Puesto:* {_escape(title)}\n"
        f"🏢 *Empresa:* {_escape(company)}\n"
        f"📍 *Ubicación:* {_escape(location)}\n"
        f"🔗 [Postularse Directamente Aquí]({_escape_url(job_url)})"
    )

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload  = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "MarkdownV2",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(endpoint, json=payload, timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get("ok"):
            log.debug(
                "send_job_alert: ✓ enviado — %s @ %s",
                title[:50], company,
            )
            return True

        log.warning(
            "send_job_alert: API error %d — %s",
            resp.status_code,
            data.get("description", resp.text[:120]),
        )
        return False

    except requests.RequestException as exc:
        log.error("send_job_alert: error de red: %s", exc)
        return False
