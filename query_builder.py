"""
query_builder.py
----------------
Construye queries optimizadas para DuckDuckGo a partir de los criterios
de búsqueda del usuario.
"""

# Mapeos de valores normalizados a términos de búsqueda
_JOB_TYPE_TERMS = {
    "remote":  '("Remote" OR "Remoto" OR "Distributed")',
    "hybrid":  '("Hybrid" OR "Híbrido")',
    "on-site": '("On-site" OR "Presencial" OR "On site")',
}

_LOCATION_TERMS = {
    "argentina":     '("Argentina" OR "AR")',
    "uruguay":       '("Uruguay" OR "UY")',
    "latin america": '("Latin America" OR "LATAM" OR "Latinoamérica")',
    "latam":         '("Latin America" OR "LATAM" OR "Latinoamérica")',
}

_SITE_FILTER = (
    "(site:greenhouse.io/jobs/ OR site:jobs.lever.co OR site:jobs.ashbyhq.com"
    " OR site:apply.workable.com OR site:*.breezy.hr/p/"
    " OR site:*.recruitee.com/o/ OR site:*.bamboohr.com/jobs/"
    " OR site:jobs.smartrecruiters.com)"
)

_EXCLUSIONS = "-inurl:privacy -inurl:terms -inurl:help"


def build_duckduckgo_query(
    tech_stack: str,
    location: str,
    job_type: str,
) -> str:
    """
    Construye una query optimizada para DuckDuckGo HTML.

    Parámetros:
        tech_stack -- tecnologías separadas por comas, ej. "Python, FastAPI"
        location   -- ubicación, ej. "Argentina", "Latam", "Latin America"
        job_type   -- modalidad: "remote", "hybrid" o "on-site"

    Retorna:
        String listo para usar como query en DuckDuckGo.
    """
    parts: list[str] = []

    # 1. Dominios ATS
    parts.append(_SITE_FILTER)

    # 2. Tecnologías — cada una entre comillas estrictas
    techs = [t.strip() for t in tech_stack.split(",") if t.strip()]
    for tech in techs:
        parts.append(f'"{tech}"')

    # 3. Modalidad
    job_type_key = job_type.strip().lower()
    if job_type_key in _JOB_TYPE_TERMS:
        parts.append(_JOB_TYPE_TERMS[job_type_key])
    else:
        parts.append(f'"{job_type}"')

    # 4. Ubicación
    location_key = location.strip().lower()
    if location_key in _LOCATION_TERMS:
        parts.append(_LOCATION_TERMS[location_key])
    else:
        parts.append(f'"{location}"')

    # 5. Exclusiones
    parts.append(_EXCLUSIONS)

    return " ".join(parts)
