# trovilo

Scraper automatizado de ofertas de trabajo en plataformas ATS. Busca posiciones en Greenhouse, Lever, Workable y Ashby usando Playwright con evasión anti-bot, y envía alertas personalizadas por Telegram a cada usuario según sus filtros guardados.

---

## Características

- **Scraping stealth** con Playwright + Chromium: rotación de User-Agent, eliminación de flags de automatización, inyección de script que neutraliza `navigator.webdriver`.
- **Búsqueda dual**: vía URL directa de Google (`scraper.py`) o simulando tipeo humano en DuckDuckGo/Google (`search_scraper.py`).
- **Deduplicación**: cada oferta se guarda en Supabase (`sent_jobs`) para no notificar la misma URL dos veces.
- **Notificaciones Telegram** en MarkdownV2 con mención a los usuarios correspondientes. Si una misma oferta coincide con varios usuarios, se envía un único mensaje mencionando a todos.
- **Dashboard web** con autenticación via Supabase Auth para gestionar filtros de búsqueda por usuario.
- **GitHub Actions**: ejecución automática cada 8 horas vía cron.

---

## Estructura del proyecto

```
trovilo/
├── main.py                          # Punto de entrada principal
├── browser.py                       # Instancia del navegador stealth (Playwright)
├── scraper.py                       # Scraping via URL directa de Google
├── search_scraper.py                # Scraping simulando tipeo humano (DDG / Google)
├── cleaners.py                      # Normalización de títulos y empresas
├── notifier.py                      # Deduplicación y notificaciones Telegram
├── index.html                       # Dashboard web con login (Supabase Auth)
├── requirements.txt                 # Dependencias Python
├── .env                             # Variables de entorno (no commitear)
├── migrations/
│   └── 001_initial_schema.sql       # Esquema de base de datos
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

Ejecutá el script de migración en el editor SQL de Supabase:

```sql
-- migrations/001_initial_schema.sql
```

Esto crea las tablas:
- **`search_filters`** — filtros de búsqueda por usuario (tech_stack, job_type, location, telegram_user).
- **`sent_jobs`** — registro de ofertas ya notificadas (deduplicación).

### 3. Usuario del dashboard

En Supabase → *Authentication → Users → Add user*, creá el usuario con el email y contraseña con los que vas a acceder a `index.html`.

---

## Uso

### Ejecutar manualmente

```bash
python main.py
```

El scraper:
1. Lee todos los filtros de `search_filters` en Supabase.
2. Abre una instancia del navegador y raspa ofertas para cada filtro.
3. Agrupa las ofertas por URL: si la misma oferta coincide con varios usuarios, envía un único mensaje de Telegram.
4. Las ofertas nuevas se persisten en `sent_jobs` y se notifican. Las duplicadas se omiten.

### Dashboard web

Abrí `index.html` en el navegador (o deployalo en Netlify/Vercel/GitHub Pages). Iniciá sesión con las credenciales de Supabase Auth y guardá los criterios de búsqueda para cada usuario de Telegram.

---

## Plataformas ATS soportadas

| Plataforma | Dominio |
|---|---|
| Greenhouse | `boards.greenhouse.io` |
| Lever | `jobs.lever.co` |
| Workable | `apply.workable.com` |
| Ashby | `jobs.ashbyhq.com` |

---

## Notificación de Telegram

Cuando se encuentra una oferta nueva, el mensaje tiene este formato:

```
📋 Posible oferta para @marcelo!

> 📌 Puesto: Senior Python Developer
> 🏢 Empresa: Acme Corp
> 🌍 Filtro: Python – Argentina
> 🔗 Ver Oferta
```

Si la misma oferta coincide con varios usuarios:

```
📋 Posible oferta para @marcelo y @juan!
...
```

Si no se encuentran resultados:

```
🔍 Sin resultados para @marcelo

> 🌍 Filtro: Python – Argentina
> No se encontraron ofertas nuevas para este criterio.
```

---

## Automatización con GitHub Actions

El workflow `.github/workflows/scraper.yml` ejecuta `main.py` automáticamente **cada 8 horas** (00:00, 08:00, 16:00 UTC).

Para configurarlo, agregá los secretos en GitHub → *Settings → Secrets and variables → Actions*:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

También podés dispararlo manualmente desde la pestaña *Actions → Run workflow*.

---

## Módulos

### `browser.py`
Instancia un navegador Chromium con configuración anti-bot. Expone el context manager `get_stealth_page(playwright)` que devuelve `(page, context)` listos para scraping. Técnicas: rotación de UA, eliminación de flags de automatización, inyección de script que neutraliza `navigator.webdriver`.

### `scraper.py`
Construye URLs de búsqueda de Google con operador `site:` para cada ATS y extrae los resultados. Funciones: `scrape_ats_with_page(page, tech, location, job_type)` (reutiliza navegador existente) y `scrape_ats(...)` (wrapper standalone).

### `search_scraper.py`
Navega a DuckDuckGo o Google, escribe la query simulando tipeo humano (caracter a caracter con jitter 80–220 ms) y extrae los resultados orgánicos. Funciones: `scrape_jobs_via_duckduckgo()` y `scrape_jobs_via_google()`.

### `cleaners.py`
Normaliza títulos de resultados de búsqueda como `"Backend Dev - Acme Corp | Lever"` en `{title: "Backend Dev", company: "Acme Corp"}`. Usa el slug de la URL para desambiguar cuál parte es el título y cuál la empresa.

### `notifier.py`
Gestiona la deduplicación contra Supabase y el envío de mensajes MarkdownV2 al Bot API de Telegram. Funciones públicas: `process_and_notify(jobs, users, tech, location)` y `notify_no_results(user, tech, location)`.

---

## Dependencias

```
supabase
requests
playwright
python-dotenv
```
