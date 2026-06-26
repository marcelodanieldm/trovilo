"""
query_builder.py
----------------
Construye queries optimizadas para DuckDuckGo a partir de los criterios
de búsqueda del usuario. Soporta batching para cubrir +70 dominios sin
exceder el límite de caracteres del motor de búsqueda.
"""

# ---------------------------------------------------------------------------
# Lista completa de dominios objetivo (ATS + job boards)
# ---------------------------------------------------------------------------

ALL_DOMAINS = [
    # ATS tradicionales
    "greenhouse.io/jobs/",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "*.breezy.hr/p/",
    "*.recruitee.com/o/",
    "*.bamboohr.com/jobs/",
    "jobs.smartrecruiters.com",
    "*.icims.com/jobs/",
    "*.taleo.net/careersection/",
    "*.myworkdayjobs.com",
    "*.jobvite.com/jobs/",
    "*.pinpointhq.com/jobs/",
    "*.personio.com/job-listings/",
    "*.dover.com/apply/",
    "*.rippling.com/jobs/",
    "*.comeet.com/jobs/",
    "*.teamtailor.com/jobs/",
    "*.recooty.com/jobs/",
    # Job boards globales remotos
    "weworkremotely.com",
    "remoteok.com",
    "remote.co/remote-jobs/",
    "remotive.com/remote-jobs/",
    "justremote.co/remote-jobs/",
    "himalayas.app/jobs/",
    "arc.dev/remote-jobs/",
    "4dayweek.io/jobs/",
    "nodesk.co/remote-jobs/",
    "jobspresso.co",
    "europeanremotejobs.com",
    "virtualvocations.com/jobs/",
    "workingnomads.com/jobs/",
    "remoteleaf.com",
    "skipthedrive.com",
    "outsourcely.com/remote-jobs/",
    "remotehub.com/job-listing/",
    # Startups / tech
    "wellfound.com/jobs/",
    "startup.jobs",
    "techinasia.com/jobs/",
    "dice.com/jobs/",
    "stackoverflow.com/jobs/",
    "builtin.com/jobs/",
    "ycombinator.com/jobs/",
    "hired.com/jobs/",
    "flexjobs.com",
    # Job boards LATAM / región
    "getonbrd.com",
    "bumeran.com.ar",
    "zonajobs.com.ar",
    "computrabajo.com.ar",
    "indeed.com/jobs/",
    "linkedin.com/jobs/",
    "glassdoor.com/Jobs/",
    # Nichos por lenguaje / stack
    "larajobs.com",
    "laravelio.jobs",
    "djangojobs.net/jobs/",
    "pythonjobs.github.io",
    "golangprojects.com/golang-jobs/",
    "rustjobs.dev/jobs/",
    "jobs.elixir.community",
    "devitjobs.com",
    "jobsintech.io",
    "techjobsforgood.com/jobs/",
    # Crypto / Web3
    "web3.career",
    "crypto.jobs",
    "blockace.io/jobs/",
    "web3board.io",
    "cryptojobslist.com",
    # Diseño / producto
    "dribbble.com/jobs/",
    "uxdesignjobs.co",
]

# ---------------------------------------------------------------------------
# Mapeos de términos de búsqueda
# ---------------------------------------------------------------------------

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

_EXCLUSIONS = "-inurl:privacy -inurl:terms -inurl:help"

MAX_QUERY_LENGTH = 1500


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------

def build_duckduckgo_query(
    tech_stack: str,
    location: str,
    job_type: str,
) -> str:
    """
    Construye una única query usando los primeros dominios ATS.
    Para cobertura completa de todos los dominios usá generate_query_batches().
    """
    site_filter = (
        "(site:greenhouse.io/jobs/ OR site:jobs.lever.co OR site:jobs.ashbyhq.com"
        " OR site:apply.workable.com OR site:*.breezy.hr/p/"
        " OR site:*.recruitee.com/o/ OR site:*.bamboohr.com/jobs/"
        " OR site:jobs.smartrecruiters.com)"
    )
    return _assemble_query(site_filter, tech_stack, location, job_type)


def generate_query_batches(
    tech_stack: str,
    location: str,
    job_type: str,
    batch_size: int = 7,
) -> list[str]:
    """
    Divide ALL_DOMAINS en lotes de `batch_size` y genera una query por lote.
    Garantiza que ninguna query supere MAX_QUERY_LENGTH caracteres.
    Si un lote supera el límite, lo divide a la mitad automáticamente.

    Retorna:
        Lista de strings de query listos para DuckDuckGo.
    """
    queries: list[str] = []

    for i in range(0, len(ALL_DOMAINS), batch_size):
        batch = ALL_DOMAINS[i : i + batch_size]
        site_filter = "(" + " OR ".join(f"site:{d}" for d in batch) + ")"
        query = _assemble_query(site_filter, tech_stack, location, job_type)

        if len(query) > MAX_QUERY_LENGTH:
            half = max(1, len(batch) // 2)
            for j in range(0, len(batch), half):
                sub_batch = batch[j : j + half]
                sub_filter = "(" + " OR ".join(f"site:{d}" for d in sub_batch) + ")"
                sub_query = _assemble_query(sub_filter, tech_stack, location, job_type)
                if len(sub_query) <= MAX_QUERY_LENGTH:
                    queries.append(sub_query)
        else:
            queries.append(query)

    return queries


# ---------------------------------------------------------------------------
# Helper interno
# ---------------------------------------------------------------------------

def _assemble_query(
    site_filter: str,
    tech_stack: str,
    location: str,
    job_type: str,
) -> str:
    """Ensambla la query: site_filter + techs + job_type + location + exclusions."""
    parts: list[str] = [site_filter]

    for tech in [t.strip() for t in tech_stack.split(",") if t.strip()]:
        parts.append(f'"{tech}"')

    parts.append(_JOB_TYPE_TERMS.get(job_type.strip().lower(), f'"{job_type}"'))
    parts.append(_LOCATION_TERMS.get(location.strip().lower(), f'"{location}"'))
    parts.append(_EXCLUSIONS)

    return " ".join(parts)
