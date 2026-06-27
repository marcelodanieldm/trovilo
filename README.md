# trovilo

Scraper masivo y automatizado de ofertas de trabajo en más de **70 plataformas ATS y job boards**. Busca posiciones en Greenhouse, Lever, Ashby, Workable, Breezy, Recruitee, BambooHR, SmartRecruiters y decenas de portales globales usando Playwright stealth, y envía alertas personalizadas por Telegram a cada usuario según sus filtros guardados en Supabase.

---

## Características

- **Scraping stealth** con Playwright + Chromium: rotación de User-Agent, eliminación de flags de automatización, neutralización de `navigator.webdriver`.
- **Motor de búsqueda masiva**: genera lotes de queries DuckDuckGo sobre 70 dominios, paginación automática (página 1 y 2), jitter anti-bot de 3–6 s entre lotes.
- **Query builder inteligente**: construye queries optimizadas con `site:` operators, tech stack en comillas estrictas, filtros de modalidad/ubicación y exclusiones automáticas.
- **Scraping resiliente**: si un lote falla por timeout o error de selector, loguea el error y continúa con el siguiente sin interrumpir la ejecución.
- **Deduplicación masiva**: upsert en bloque en Supabase (`sent_jobs`) para nunca notificar la misma URL dos veces.
- **Notificaciones Telegram** en MarkdownV2 con rate-limit de 1 msg/s para no exceder el límite de la API.
- **Dashboard web** (`index.html`) con autenticación via Supabase Auth para gestionar filtros de búsqueda por usuario y **expandir fuentes** importando nuevos job boards desde Excel/CSV o entrada manual.
- **GitHub Actions**: ejecución automática cada 8 horas vía cron.

---

## Estructura del proyecto

```
trovilo/
├── main.py                          # Orquestador principal
├── browser.py                       # Navegador stealth (Playwright + Chromium)
├── scraper.py                       # Motor de scraping masivo vía DuckDuckGo
├── query_builder.py                 # Generador de queries en lotes (70 dominios)
├── cleaners.py                      # Normalización de títulos y empresas
├── notifier.py                      # Deduplicación (Supabase) y alertas Telegram
├── search_scraper.py                # Scraping alternativo con tipeo humano simulado
├── index.html                       # Dashboard web (Supabase Auth + gestión de filtros)
├── requirements.txt                 # Dependencias Python
├── .env                             # Variables de entorno (no commitear)
├── migrations/
│   ├── 001_initial_schema.sql       # Tablas search_filters y sent_jobs
│   └── 002_fuentes_scraper.sql      # Tabla fuentes_scraper (dominios personalizados)
└── .github/
    └── workflows/
        └── scraper.yml              # Workflow de GitHub Actions
```

---

## Requisitos

- Python 3.11+
- Cuenta en [Supabase](https://supabase.com)
- Bot de Telegram (via [@BotFather](https://t.me/BotFather))

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/marcelodanieldm/trovilo.git
cd trovilo

# 2. Crear y activar el entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1       # Windows PowerShell
# source .venv/bin/activate        # Linux / macOS

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar el navegador Chromium de Playwright
playwright install chromium
```

> **Windows**: si PowerShell bloquea la activación, ejecutá `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` una vez.

> **Chromium en disco D:** si querés instalar los binarios en otra unidad:
> ```powershell
> [System.Environment]::SetEnvironmentVariable("PLAYWRIGHT_BROWSERS_PATH", "D:\playwright-browsers", "User")
> playwright install chromium
> ```

---

## Configuración

### 1. Variables de entorno

Copiá `.env` y completá con tus credenciales:

```env
SUPABASE_URL=https://TU_PROJECT_ID.supabase.co
SUPABASE_KEY=tu-anon-key

TELEGRAM_BOT_TOKEN=123456789:ABC-tu-token
TELEGRAM_CHAT_ID=tu-chat-id
```

| Variable | Dónde obtenerla |
|---|---|
| `SUPABASE_URL` | Supabase → *Settings → API → Project URL* |
| `SUPABASE_KEY` | Supabase → *Settings → API → anon public key* |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | `https://api.telegram.org/bot<TOKEN>/getUpdates` tras enviarle un mensaje al bot |

### 2. Base de datos

Ejecutá los scripts de migración en el editor SQL de Supabase en orden:

```sql
-- 1. Tablas principales
-- migrations/001_initial_schema.sql

-- 2. Tabla de fuentes personalizadas
-- migrations/002_fuentes_scraper.sql
```

Tablas creadas:

| Tabla | Descripción |
|---|---|
| `search_filters` | Filtros de búsqueda por usuario (tech_stack, job_type, location, telegram_user) |
| `sent_jobs` | Registro de ofertas ya notificadas (deduplicación por job_url) |
| `fuentes_scraper` | Dominios personalizados agregados desde el dashboard |

### 3. Usuario del dashboard

En Supabase → *Authentication → Users → Add user*, creá el usuario con el email y contraseña con los que vas a acceder a `index.html`.

---

## Uso

### Ejecutar manualmente

```bash
python main.py
```

El orquestador:
1. Lee todos los filtros de `search_filters` en Supabase.
2. Abre una única instancia del navegador stealth.
3. Por cada filtro activo, ejecuta el scraping masivo sobre los 70 dominios en lotes.
4. Deduplica las ofertas contra Supabase y envía alertas de Telegram.
5. Cierra el navegador en un bloque `finally`, incluso si ocurre un error.

### Dashboard web

Abrí `index.html` en el navegador (o deployalo en Netlify/Vercel/GitHub Pages). Iniciá sesión con las credenciales de Supabase Auth para:

- **Gestionar filtros** de búsqueda por usuario de Telegram.
- **Expandir fuentes**: importar nuevos job boards desde un archivo CSV/Excel o pegando URLs manualmente. Los dominios se validan contra `fuentes_scraper` y los nuevos se pueden agregar con un click.

---

## Plataformas ATS y job boards cubiertos

El sistema scrapeа **70 dominios** organizados por categoría:

| Categoría | Ejemplos |
|---|---|
| ATS tradicionales | Greenhouse, Lever, Ashby, Workable, Breezy, Recruitee, BambooHR, SmartRecruiters, iCIMS, Taleo, Workday, Jobvite, Teamtailor, Personio |
| Job boards remotos | We Work Remotely, RemoteOK, Remote.co, Remotive, Himalayas, Arc.dev, FlexJobs, Working Nomads |
| Startups / tech | Wellfound (AngelList), Hacker News Jobs (YC), Dice, Stack Overflow Jobs, BuiltIn, Hired |
| LATAM / región | Getonbrd, Bumeran, ZonaJobs, Computrabajo, Indeed, LinkedIn, Glassdoor |
| Nicho por stack | LaraJobs, DjangoJobs, GolangProjects, RustJobs, DevITJobs |
| Crypto / Web3 | Web3.career, Crypto.jobs, CryptoJobsList |

---

## Cómo funciona el scraping

1. **`query_builder.py`** divide los 70 dominios en lotes de 10 y construye queries con operadores `site:` de DuckDuckGo, ej:
   ```
   (site:greenhouse.io OR site:jobs.lever.co OR ...) "Python" "Django" ("Remote" OR "Remoto") ("Argentina" OR "AR") -inurl:privacy -inurl:terms
   ```
2. **`scraper.py`** navega a `https://html.duckduckgo.com/html/?q={query}` por cada lote. Si hay botón "Next" (`.nav-link form`), extrae también la página 2. Cada URL se valida con `is_valid_job_url()` antes de incluirse en los resultados.
3. **`notifier.py`** hace un upsert masivo en `sent_jobs` usando `on_conflict='job_url'` para deduplicar, y luego envía un mensaje MarkdownV2 al bot de Telegram con `sleep(1)` entre mensajes.

---

## Notificación de Telegram

Cuando se encuentra una oferta nueva:

```
🚨 ¡Nueva Oferta Encontrada!

> 📌 Puesto: Senior Python Developer
> 🏢 Empresa: Acme Corp
> 🌍 Filtro: Python – Argentina
> 👤 Responsable: @marcelo
> 🔗 Ver Oferta
```

Si no se encuentran resultados nuevos para un filtro:

```
🔍 Sin resultados para @marcelo

> 🌍 Filtro: Python – Argentina
> No se encontraron ofertas nuevas para este criterio.
```

---

## Automatización con GitHub Actions

El workflow `.github/workflows/scraper.yml` ejecuta `main.py` automáticamente **cada 8 horas** (00:00, 08:00, 16:00 UTC).

Agregá los secretos en GitHub → *Settings → Secrets and variables → Actions*:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

También podés dispararlo manualmente desde *Actions → Run workflow*.

---

## Módulos

### `main.py`
Orquestador principal. Descarga filtros de Supabase, inicializa un único browser stealth, itera sobre los filtros ejecutando scraping + notificaciones, y garantiza el cierre del browser con `finally`.

### `browser.py`
Instancia Chromium con configuración anti-bot. Expone el context manager `get_stealth_page(playwright)` → `(page, context)`. Técnicas: rotación de UA, eliminación de flags de automatización, inyección de script que neutraliza `navigator.webdriver`.

### `scraper.py`
Motor de scraping masivo. Función principal `execute_unbreakable_scraping(page, tech, location, job_type)`: navega por URL directa a DuckDuckGo HTML, extrae resultados de página 1 y 2, maneja errores por lote con `continue`, aplica jitter de 3–6 s entre batches. Incluye `is_valid_job_url()` para filtrar falsos positivos y `_extract_company()` para resolver el nombre de empresa desde la URL sin hardcodear plataformas.

### `query_builder.py`
Generador de queries DuckDuckGo. Define `ALL_DOMAINS` (70 dominios), `generate_duckduckgo_batches()` (generador por lotes con corte a 1500 chars), y helpers `build_duckduckgo_query()` / `generate_query_batches()` para compatibilidad.

### `cleaners.py`
Normaliza títulos del tipo `"Backend Dev - Acme Corp | Lever"` en `{title, company}`. Usa el slug de la URL para desambiguar.

### `notifier.py`
Deduplicación y notificaciones. `bulk_filter_and_save(jobs_list)` hace un upsert masivo y retorna solo las ofertas realmente nuevas. `process_and_notify()` las notifica respetando el rate-limit de Telegram (1 msg/s).

### `search_scraper.py`
Módulo alternativo que navega a DuckDuckGo/Google y escribe la query simulando tipeo humano (caracter a caracter con jitter 80–220 ms).

---

## Dependencias

```
supabase
requests
playwright
python-dotenv
```

