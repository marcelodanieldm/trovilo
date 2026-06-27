"""
company_discovery.py
--------------------
Descubre automáticamente empresas nuevas que publican ofertas en Greenhouse
o Lever haciendo búsquedas con filtro site: en DuckDuckGo Lite via Playwright.
Luego persiste las empresas nuevas en la tabla 'target_companies' de Supabase.

Flujo:
  1. Construye queries DDG Lite con site:boards.greenhouse.io y site:jobs.lever.co.
  2. Navega con el browser stealth (anti-bot) y extrae los href de resultados.
  3. Decodifica URLs de redirección de DDG (formato //duckduckgo.com/l/?uddg=...).
  4. Aplica regex para aislar el company_id (primer segmento de path).
  5. Retorna lista de dicts listos para insertar en target_companies.
  6. save_discovered_companies() cruza contra Supabase y persiste solo las nuevas.

Uso:
    from company_discovery import discover_new_ats_companies, save_discovered_companies
    found = discover_new_ats_companies("QA")
    save_discovered_companies(found)
"""
import re
import logging
import time
import random
from urllib.parse import unquote, urlparse, parse_qs
from playwright.sync_api import sync_playwright
from browser import get_stealth_page

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patrones de URL para cada ATS
# ---------------------------------------------------------------------------

# Greenhouse usa dos dominios: boards. (antiguo) y job-boards. (nuevo)
_GH_RE = re.compile(
    r'https?://(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)',
    re.IGNORECASE,
)
# Lever usa un único dominio canónico
_LV_RE = re.compile(
    r'https?://jobs\.lever\.co/([a-zA-Z0-9_-]+)',
    re.IGNORECASE,
)

# UUID completo: Lever embebe IDs de oferta como primer segmento a veces
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

# Segmentos que no representan company IDs
_SKIP_SLUGS = frozenset({
    '', 'jobs', 'embed', 'boards', 'board', 'api', 'v1', 'v2',
    'search', 'login', 'logout', 'settings', 'privacy', 'terms',
    'apply', 'careers', 'open-positions',
})

# ---------------------------------------------------------------------------
# Configuración de queries
# ---------------------------------------------------------------------------

_DDG_LITE = "https://duckduckgo.com/lite/?q="

def _build_queries(tech_keyword: str) -> list[tuple[str, str]]:
    """
    Genera pares (url_de_busqueda, ats_type) para el keyword dado.
    Incluye ambos dominios de Greenhouse (boards. y job-boards.).
    """
    kw = tech_keyword.strip().replace(" ", "+")
    return [
        (
            f'{_DDG_LITE}site%3Aboards.greenhouse.io+{kw}+%22Remote%22',
            "greenhouse",
        ),
        (
            f'{_DDG_LITE}site%3Ajob-boards.greenhouse.io+{kw}+%22Remote%22',
            "greenhouse",
        ),
        (
            f'{_DDG_LITE}site%3Ajobs.lever.co+{kw}+%22Remote%22',
            "lever",
        ),
    ]

# ---------------------------------------------------------------------------
# Decodificación de URLs de DuckDuckGo
# ---------------------------------------------------------------------------

def _decode_ddg_href(href: str) -> str:
    """
    DDG Lite envuelve los links externos en una URL de redirección propia:
      //duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Fvtex&rut=...

    Esta función extrae y decodifica la URL real del parámetro 'uddg'.
    Si el href ya es una URL directa, la devuelve sin cambios.
    """
    if not href:
        return ""

    # Normalizar URLs que empiezan con //
    if href.startswith("//"):
        href = "https:" + href

    if "duckduckgo.com/l/" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            uddg = qs.get("uddg", [""])[0]
            if uddg:
                return unquote(uddg)
        except Exception:
            pass

    return href

# ---------------------------------------------------------------------------
# Extracción de company_id
# ---------------------------------------------------------------------------

def _extract_company_id(url: str, ats_type: str) -> str | None:
    """
    Aplica el regex correspondiente al ATS y devuelve el company_id si es válido.
    Descarta slugs de sistema, UUIDs y valores puramente numéricos.

    Ejemplos:
        "https://boards.greenhouse.io/vercel/jobs/5999792004" → "vercel"
        "https://jobs.lever.co/vtex/abc123-def4-..."         → "vtex"
        "https://job-boards.greenhouse.io/stripe"            → "stripe"
    """
    pattern = _GH_RE if ats_type == "greenhouse" else _LV_RE
    match   = pattern.search(url)
    if not match:
        return None

    slug = match.group(1).lower().strip()

    if slug in _SKIP_SLUGS:
        return None
    if slug.isdigit():
        return None
    if _UUID_RE.match(slug):
        return None
    if len(slug) < 2:
        return None

    return slug

# ---------------------------------------------------------------------------
# Scraping de resultados DDG Lite con Playwright
# ---------------------------------------------------------------------------

def _scrape_ddg_page(page, search_url: str, ats_type: str) -> list[str]:
    """
    Navega a la URL de DDG Lite y extrae los href de los resultados.
    Detecta bloqueos (CAPTCHA / sin resultados) y registra advertencias.

    Estrategia en capas:
      1. Selector CSS: a.result-link
      2. Fallback regex sobre el HTML crudo (captura incluso con JS desactivado)
    """
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=25_000)
        # Espera aleatoria para imitar lectura humana
        page.wait_for_timeout(random.randint(2_000, 4_000))
    except Exception as exc:
        log.warning("[discovery] Error navegando a DDG: %s", exc)
        return []

    html = page.content()

    # Detección de bloqueos
    lower_html = html.lower()
    if any(token in lower_html for token in ("captcha", "bots use duckduckgo", "challenge")):
        log.warning("[discovery] CAPTCHA / bloqueo detectado para: %s", search_url)
        return []

    hrefs: list[str] = []

    # --- Capa 1: selector CSS ---
    try:
        link_els = page.query_selector_all("a.result-link")
        for el in link_els:
            raw = el.get_attribute("href") or ""
            decoded = _decode_ddg_href(raw)
            if decoded:
                hrefs.append(decoded)
    except Exception as exc:
        log.debug("[discovery] Fallo selector CSS: %s", exc)

    # --- Capa 2: regex sobre HTML crudo (fallback) ---
    if not hrefs:
        # Busca tanto URLs directas como redireccionadas por DDG
        if ats_type == "greenhouse":
            hrefs = re.findall(
                r'href="((?:https?:)?//(?:[^"]*greenhouse\.io)[^"]*)"',
                html,
            )
        else:
            hrefs = re.findall(
                r'href="((?:https?:)?//(?:[^"]*lever\.co)[^"]*)"',
                html,
            )
        hrefs = [_decode_ddg_href(h) for h in hrefs]

    log.debug("[discovery] %d href(s) extraídos de %s", len(hrefs), search_url)
    return hrefs

# ---------------------------------------------------------------------------
# Función principal pública
# ---------------------------------------------------------------------------

def discover_new_ats_companies(tech_keyword: str = "QA") -> list[dict]:
    """
    Descubre empresas que publican ofertas en Greenhouse / Lever usando
    búsquedas con filtro site: en DuckDuckGo Lite.

    Para cada URL encontrada extrae el company_id (primer segmento del path),
    que es el mismo slug que usan los endpoints públicos de las APIs:
      - Greenhouse: boards-api.greenhouse.io/v1/boards/{ats_id}/jobs
      - Lever:      api.lever.co/v0/postings/{ats_id}?mode=json

    Parámetros:
        tech_keyword: término de búsqueda adicional (ej. "QA", "Python", "React")

    Retorna:
        Lista de dicts únicos por ats_id:
        [
            {'company_name': 'vtex',   'ats_type': 'lever',      'ats_id': 'vtex'},
            {'company_name': 'vercel', 'ats_type': 'greenhouse', 'ats_id': 'vercel'},
            ...
        ]
    """
    log.info("[discovery] Iniciando búsqueda — keyword: '%s'", tech_keyword)

    queries      = _build_queries(tech_keyword)
    seen_ids:  set[str]   = set()
    results:   list[dict] = []

    pw_ctx = sync_playwright().start()
    try:
        with get_stealth_page(pw_ctx) as (page, _ctx):
            for idx, (search_url, ats_type) in enumerate(queries, start=1):
                log.info(
                    "[discovery] Query %d/%d [%s]: %s",
                    idx, len(queries), ats_type, search_url,
                )

                hrefs = _scrape_ddg_page(page, search_url, ats_type)
                log.info("[discovery]   → %d link(s) encontrado(s).", len(hrefs))

                for href in hrefs:
                    cid = _extract_company_id(href, ats_type)
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        results.append({
                            "company_name": cid,
                            "ats_type":     ats_type,
                            "ats_id":       cid,
                        })
                        log.debug("[discovery]   + %s (%s)", cid, ats_type)

                # Jitter entre queries para no sobrecargar DDG
                if idx < len(queries):
                    wait = random.uniform(5.0, 9.0)
                    log.debug("[discovery] Esperando %.1f s antes de la siguiente query.", wait)
                    time.sleep(wait)

    finally:
        try:
            pw_ctx.stop()
        except Exception:
            pass

    log.info("[discovery] Finalizado — %d empresa(s) nueva(s) encontrada(s).", len(results))
    return results


# ---------------------------------------------------------------------------
# Persistencia en Supabase
# ---------------------------------------------------------------------------

def save_discovered_companies(discovered_list: list[dict]) -> int:
    """
    Persiste en 'target_companies' las empresas descubiertas,
    insertando solo las que aún no existen (sin pisar datos previos).

    Estrategia:
      1. Consulta los ats_id ya presentes en la tabla.
      2. Filtra la lista recibida para identificar las genuinamente nuevas.
      3. Hace upsert con ignore_duplicates=True (→ ON CONFLICT DO NOTHING)
         como segunda línea de defensa ante race conditions.

    El campo 'active' queda en TRUE por su DEFAULT en Postgres;
    'created_at' se asigna automáticamente con NOW().

    Parámetros:
        discovered_list: salida de discover_new_ats_companies().
                         Cada dict debe tener: company_name, ats_type, ats_id.

    Retorna:
        Número de empresas nuevas efectivamente insertadas (0 si ninguna).
    """
    if not discovered_list:
        log.info("[discovery] save: lista vacía, nada que guardar.")
        return 0

    # Importación diferida para no requerir variables de entorno al importar el módulo
    from notifier import supabase

    # 1. Obtener todos los ats_id ya registrados en la tabla
    try:
        resp     = supabase.table("target_companies").select("ats_id").execute()
        existing = {row["ats_id"] for row in (resp.data or [])}
    except Exception as exc:
        log.error("[discovery] save: error consultando target_companies: %s", exc)
        return 0

    # 2. Aislar las empresas que todavía no conocemos
    new_companies = [c for c in discovered_list if c.get("ats_id") not in existing]

    known_count = len(discovered_list) - len(new_companies)
    log.info(
        "[discovery] save: %d descubiertas | %d ya en BD | %d nuevas para insertar.",
        len(discovered_list),
        known_count,
        len(new_companies),
    )

    if not new_companies:
        log.info("[discovery] save: sin empresas nuevas. Base de datos actualizada.")
        return 0

    # 3. Upsert con ignore_duplicates=True → ON CONFLICT (ats_id) DO NOTHING
    #    Actúa como segunda defensa en caso de race condition o ejecuciones paralelas.
    try:
        supabase.table("target_companies").upsert(
            new_companies,
            on_conflict="ats_id",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        log.error("[discovery] save: error en upsert: %s", exc)
        return 0

    log.info(
        "[discovery] save: ✓ %d empresa(s) nueva(s) agregada(s) a target_companies.",
        len(new_companies),
    )
    for c in new_companies:
        log.info("  + %-20s [%s]", c["ats_id"], c["ats_type"])

    return len(new_companies)


# ---------------------------------------------------------------------------
# Punto de entrada — usado por GitHub Actions y ejecución local
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    # Leer keyword desde variable de entorno (GitHub Actions) o argumento CLI
    keyword = (
        os.environ.get("DISCOVERY_KEYWORD")
        or (sys.argv[1] if len(sys.argv) > 1 else "")
        or "QA"
    )

    log.info("══════════════════════════════════════")
    log.info("Trovilo — Company Discovery")
    log.info("Keyword: '%s'", keyword)
    log.info("══════════════════════════════════════")

    discovered = discover_new_ats_companies(keyword)

    if not discovered:
        log.info("Ninguna empresa descubierta en esta ejecución.")
        sys.exit(0)

    added = save_discovered_companies(discovered)

    log.info("══════════════════════════════════════")
    log.info("Resumen: %d descubiertas / %d nuevas insertadas.", len(discovered), added)
    log.info("══════════════════════════════════════")
