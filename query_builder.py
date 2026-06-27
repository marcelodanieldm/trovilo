"""
query_builder.py
----------------
Construye queries optimizadas para DuckDuckGo a partir de los criterios
de búsqueda del usuario. Usa un generador para iterar lotes sin cargar
todo en memoria y garantiza que cada query quede bajo el umbral de DDG.
"""
from typing import Generator

# ---------------------------------------------------------------------------
# Lista completa de 70 dominios objetivo (ATS + job boards)
# ---------------------------------------------------------------------------

ALL_DOMAINS = [
    # ATS tradicionales
    "greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "breezy.hr",
    "recruitee.com",
    "bamboohr.com",
    "jobs.smartrecruiters.com",
    "icims.com",
    "taleo.net",
    "myworkdayjobs.com",
    "jobvite.com",
    "pinpointhq.com",
    "personio.com",
    "dover.io",
    "rippling.com",
    "comeet.com",
    "teamtailor.com",
    "recooty.com",
    # Job boards remotos globales
    "weworkremotely.com",
    "remoteok.com",
    "remote.co",
    "remotive.com",
    "justremote.co",
    "himalayas.app",
    "arc.dev",
    "4dayweek.io",
    "nodesk.co",
    "jobspresso.co",
    "europeanremotejobs.com",
    "virtualvocations.com",
    "workingnomads.com",
    "remoteleaf.com",
    "skipthedrive.com",
    "outsourcely.com",
    "remotehub.com",
    "flexjobs.com",
    # Startups / tech
    "wellfound.com",
    "angel.co",
    "startup.jobs",
    "techinasia.com",
    "dice.com",
    "stackoverflow.com",
    "builtin.com",
    "ycombinator.com",
    "hired.com",
    "triplebyte.com",
    # Job boards LATAM / región
    "getonbrd.com",
    "bumeran.com.ar",
    "zonajobs.com.ar",
    "computrabajo.com.ar",
    "indeed.com",
    "linkedin.com",
    "glassdoor.com",
    # Nichos por stack / lenguaje
    "larajobs.com",
    "djangojobs.net",
    "golangprojects.com",
    "rustjobs.dev",
    "devitjobs.com",
    "jobsintech.io",
    "techjobsforgood.com",
    # Crypto / Web3
    "web3.career",
    "crypto.jobs",
    "cryptojobslist.com",
    "web3board.io",
    "blockace.io",
    # Diseño / producto
    "dribbble.com",
    "uxdesignjobs.co",
    "productboard.com",
]

# ---------------------------------------------------------------------------
# Mapeos de términos de búsqueda
# ---------------------------------------------------------------------------

_JOB_TYPE_TERMS: dict[str, str] = {
    "remote":  '("Remote" OR "Remoto" OR "Distributed")',
    "hybrid":  '("Hybrid" OR "Híbrido")',
    "on-site": '("On-site" OR "Presencial" OR "On site")',
}

_LOCATION_TERMS: dict[str, str] = {
    "argentina":     '("Argentina" OR "AR")',
    "uruguay":       '("Uruguay" OR "UY")',
    "latin america": '("Latin America" OR "LATAM" OR "Latinoamérica")',
    "latam":         '("Latin America" OR "LATAM" OR "Latinoamérica")',
}

_EXCLUSIONS = "-inurl:privacy -inurl:terms -inurl:help"
MAX_QUERY_LENGTH = 1500


# ---------------------------------------------------------------------------
# Sanitización
# ---------------------------------------------------------------------------

def _sanitize(text: str) -> str:
    """Escapa comillas dobles internas para no romper la sintaxis de DDG."""
    return text.replace('"', '\\"').strip()


# ---------------------------------------------------------------------------
# Generador principal
# ---------------------------------------------------------------------------

def generate_duckduckgo_batches(
    tech: str,
    location: str,
    job_type: str,
    batch_size: int = 10,
) -> Generator[str, None, None]:
    """
    Generador que produce queries de DuckDuckGo en lotes.

    Por cada lote de `batch_size` dominios construye:
      (site:domain1.com OR site:domain2.com OR ...) "tech1" "tech2" JOBTYPE LOCATION -inurl:...

    Si un lote genera una query mayor que MAX_QUERY_LENGTH la divide a la mitad
    y emite los sub-lotes por separado.

    Yields:
        str — query lista para enviar a DuckDuckGo HTML.
    """
    # Construir sufijo fijo (techs + job_type + location + exclusiones)
    suffix = _build_suffix(tech, location, job_type)

    for i in range(0, len(ALL_DOMAINS), batch_size):
        batch = ALL_DOMAINS[i : i + batch_size]
        query = _build_query(batch, suffix)

        if len(query) <= MAX_QUERY_LENGTH:
            yield query
        else:
            # Dividir el lote a la mitad si supera el umbral
            half = max(1, len(batch) // 2)
            for j in range(0, len(batch), half):
                sub = batch[j : j + half]
                sub_query = _build_query(sub, suffix)
                if len(sub_query) <= MAX_QUERY_LENGTH:
                    yield sub_query


# ---------------------------------------------------------------------------
# Alias para compatibilidad con scraper.py
# ---------------------------------------------------------------------------

def generate_query_batches(
    tech_stack: str,
    location: str,
    job_type: str,
    batch_size: int = 10,
) -> list[str]:
    """Wrapper que consume el generador y retorna una lista (compatibilidad)."""
    return list(generate_duckduckgo_batches(tech_stack, location, job_type, batch_size))


def build_duckduckgo_query(
    tech_stack: str,
    location: str,
    job_type: str,
) -> str:
    """Retorna la primera query del generador (ATS principales)."""
    return next(generate_duckduckgo_batches(tech_stack, location, job_type, batch_size=8), "")


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _build_suffix(tech: str, location: str, job_type: str) -> str:
    """Construye el sufijo compartido por todos los lotes."""
    parts: list[str] = []

    for t in [_sanitize(t) for t in tech.split(",") if t.strip()]:
        parts.append(f'"{t}"')

    parts.append(_JOB_TYPE_TERMS.get(job_type.strip().lower(), f'"{_sanitize(job_type)}"'))
    parts.append(_LOCATION_TERMS.get(location.strip().lower(), f'"{_sanitize(location)}"'))
    parts.append(_EXCLUSIONS)

    return " ".join(parts)


def _build_query(domains: list[str], suffix: str) -> str:
    """Une los dominios con OR, los agrupa en paréntesis y añade el sufijo."""
    site_filter = "(" + " OR ".join(f"site:{d}" for d in domains) + ")"
    return f"{site_filter} {suffix}"
