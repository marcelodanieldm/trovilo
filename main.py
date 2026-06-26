"""
main.py
-------
Punto de entrada principal de trovilo.

Flujo de ejecución:
  1. Carga variables de entorno desde `.env`.
  2. Lee todos los filtros de búsqueda activos desde Supabase (`search_filters`).
  3. Abre una única instancia del navegador stealth (Playwright + Chromium).
  4. Para cada filtro, ejecuta el scraping de ofertas en plataformas ATS.
  5. Agrupa las ofertas por URL: si una misma oferta coincide con los
     filtros de varios usuarios, se genera un único mensaje de Telegram
     mencionando a todos ellos.
  6. Envía notificaciones vía Telegram y persiste las ofertas en Supabase.

Uso:
    python main.py

Programado vía GitHub Actions (ver .github/workflows/scraper.yml).
"""
import os
from scraper import scrape_ats_with_page
from notifier import supabase, process_and_notify, notify_no_results

# Cargar las variables de entorno desde el archivo .env
load_dotenv()


def fetch_search_filters() -> list[dict]:
    """
    Obtiene todas las filas de la tabla 'search_filters' en Supabase.
    Devuelve una lista de dicts con los parámetros de búsqueda de cada usuario.
    """
    resultado = supabase.table("search_filters").select("*").execute()
    return resultado.data or []


def run() -> None:
    """
    Flujo principal del scraper:
      1. Carga los filtros de búsqueda desde Supabase.
      2. Abre una única instancia del navegador stealth.
      3. Raspa ofertas por cada filtro y las agrupa por URL.
      4. Por cada URL única notifica a todos los usuarios que coincidieron.
      5. Cierra el navegador al finalizar, incluso si ocurre un error.
    """
    print("[main] Iniciando trovilo...")

    try:
        filtros = fetch_search_filters()
    except Exception as e:
        print(f"[main] Error al obtener filtros de Supabase: {e}")
        return

    if not filtros:
        print("[main] No se encontraron filtros de búsqueda. Saliendo.")
        return

    print(f"[main] {len(filtros)} filtro(s) encontrado(s).")

    # Agrupa ofertas por URL y acumula los usuarios que coincidieron con cada una.
    # Estructura: { job_url: {'job': {...}, 'users': [...], 'tech': str, 'location': str} }
    jobs_map: dict[str, dict] = {}

    with sync_playwright() as pw:
        with get_stealth_page(pw) as (page, _context):

            for filtro in filtros:
                tech          = filtro.get("tech_stack", "").strip()
                location      = filtro.get("location", "").strip()
                job_type      = filtro.get("job_type", "").strip()
                telegram_user = filtro.get("telegram_user", "").strip()

                if not all([tech, location, job_type, telegram_user]):
                    print(f"[main] Filtro incompleto, omitiendo: {filtro}")
                    continue

                print(
                    f"[main] Buscando — tech: '{tech}' | "
                    f"ubicación: '{location}' | modalidad: '{job_type}' | "
                    f"usuario: '{telegram_user}'"
                )

                try:
                    jobs = scrape_ats_with_page(page, tech, location, job_type)
                    print(f"[main] {len(jobs)} oferta(s) para '{telegram_user}'.")

                    if not jobs:
                        # Sin resultados — notificar de inmediato a este usuario
                        notify_no_results(telegram_user, tech, location)
                        continue

                    # Acumular en el mapa; si la misma URL coincide con varios
                    # usuarios se listan todos en el mismo mensaje
                    for job in jobs:
                        url = job.get("url", "").strip()
                        if not url:
                            continue
                        if url not in jobs_map:
                            jobs_map[url] = {
                                "job":      job,
                                "users":    [],
                                "tech":     tech,
                                "location": location,
                            }
                        if telegram_user not in jobs_map[url]["users"]:
                            jobs_map[url]["users"].append(telegram_user)

                except Exception as e:
                    print(f"[main] Error procesando filtro de '{telegram_user}': {e}")

    # Una notificación por oferta única, mencionando a todos los usuarios
    print(f"[main] {len(jobs_map)} oferta(s) única(s) a procesar.")
    for url, data in jobs_map.items():
        process_and_notify(
            [data["job"]],
            users=data["users"],
            tech=data["tech"],
            location=data["location"],
        )

    print("[main] Scraping finalizado. Navegador cerrado.")


if __name__ == "__main__":
    run()
