"""
cleaners.py
-----------
Normaliza los títulos de resultados de búsqueda provenientes de
plataformas ATS (Greenhouse, Lever, Workable, Ashby).

Los motores de búsqueda devuelven títulos con ruido de plataforma
como "Backend Dev - Acme Corp | Lever" o "QA Engineer at Acme - Greenhouse".
Este módulo los limpia y separa en `title` y `company`.

Función principal:
  clean_google_result(title_text, url) -> {'title': str, 'company': str}
      Aplica stripping de sufijos, detección de separadores y
      desambiguación usando el slug de empresa de la URL.
      Funciona tanto para resultados de Google como de DuckDuckGo.
"""
import re

# ---------------------------------------------------------------------------
# Patrones de ruido al final del título según plataforma ATS
# Se aplican en orden — los más específicos primero
# ---------------------------------------------------------------------------
_TRAILING_NOISE = [
    r"\s*[\|\-–]\s*Job Board\b.*$",
    r"\s*[\|\-–]\s*Greenhouse\b.*$",
    r"\s*[\|\-–]\s*Lever\b.*$",
    r"\s*[\|\-–]\s*Workable\b.*$",
    r"\s*[\|\-–]\s*Ashby\s*HQ\b.*$",
    r"\s*[\|\-–]\s*Ashby\b.*$",
    r"\s*[\|\-–]\s*Jobs\b\s*$",
    r"\s*\|\s*Apply\b.*$",
]

# Compilar todos los patrones una sola vez al importar el módulo
_NOISE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _TRAILING_NOISE]

# Separadores entre título y empresa, de más a menos específico
_SEPARATORS = [" at ", " @ ", " | ", " - ", " – "]


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _strip_noise(text: str) -> str:
    """Elimina sufijos de plataforma al final del título."""
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("", text).strip()
    return text


def _company_from_url(url: str) -> str:
    """
    Extrae el slug de empresa desde la URL y lo convierte a texto legible.
    Patrón: https://dominio.com/empresa/...  →  parts[3]
    Devuelve cadena vacía si la URL no tiene suficientes segmentos.
    """
    try:
        parts = url.rstrip("/").split("/")
        slug = parts[3] if len(parts) > 3 else ""
        return slug.replace("-", " ").strip()
    except Exception:
        return ""


def _split_title_company(text: str, url_company: str) -> tuple[str, str]:
    """
    Intenta separar el texto limpio en (título, empresa).

    Estrategia:
      1. Busca separadores en orden de especificidad.
      2. Si el slug de empresa de la URL coincide con alguno de los fragmentos,
         ese fragmento es la empresa (sin importar el orden).
      3. Si no hay coincidencia clara, asume el formato 'Título - Empresa'.
      4. Si no hay separador, devuelve el texto completo como título.
    """
    hint = url_company.lower()

    for sep in _SEPARATORS:
        if sep in text:
            left, right = text.split(sep, 1)
            left, right = left.strip(), right.strip()

            if not left or not right:
                continue

            # Usar el slug de la URL como desambiguador
            if hint and hint in right.lower():
                return left, right.title()
            if hint and hint in left.lower():
                return right, left.title()

            # Sin coincidencia clara: el lado derecho suele ser la empresa
            return left, right

    # Sin separador encontrado — todo es el título
    return text, url_company.title() if url_company else "Desconocida"


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def clean_google_result(title_text: str, url: str) -> dict:
    """
    Parsea el texto de un resultado de búsqueda (Google o DuckDuckGo)
    proveniente de plataformas ATS y devuelve título y empresa limpios.

    Maneja los formatos comunes de cada plataforma:
      - Lever:      "Backend Developer - Acme Corp | Lever"
      - Greenhouse: "Senior Engineer at Acme Corp - Greenhouse"
      - Workable:   "Frontend Dev at Acme Corp | Workable"
      - Ashby:      "Data Engineer - Acme Corp | Ashby"

    Parámetros:
        title_text -- texto del título tal como lo devuelve el motor de búsqueda
        url        -- URL completa de la oferta (se usa para identificar la empresa)

    Retorna:
        {'title': str, 'company': str}
    """
    if not title_text:
        return {"title": "Sin título", "company": _company_from_url(url).title()}

    # Paso 1: eliminar sufijos de plataforma al final del texto
    cleaned = _strip_noise(title_text)

    # Paso 2: obtener el slug de empresa desde la URL como pista
    url_company = _company_from_url(url)

    # Paso 3: separar título de empresa
    title, company = _split_title_company(cleaned, url_company)

    # Paso 4: normalizar capitalización
    return {
        "title":   title.strip(),
        "company": company.strip(),
    }
