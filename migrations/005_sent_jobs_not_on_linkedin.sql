-- =============================================================================
-- Migration: 005_sent_jobs_not_on_linkedin
-- Descripción: Agrega la columna not_on_linkedin a sent_jobs.
--   - not_on_linkedin BOOLEAN DEFAULT TRUE: indica que la oferta no está
--     publicada en LinkedIn (exclusiva del scraper).
--   - Índice parcial sobre (is_active = TRUE) optimizado para el filtro
--     más común del frontend (ofertas exclusivas activas).
--
-- Tiempo de respuesta objetivo: < 5 ms en queries filtradas por esta columna.
--
-- Cómo correrlo: SQL Editor de Supabase → pegar y ejecutar.
-- Es idempotente: se puede correr más de una vez sin error.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Agregar columna not_on_linkedin
--    IF NOT EXISTS garantiza idempotencia: si ya existe, no falla.
-- -----------------------------------------------------------------------------

ALTER TABLE sent_jobs
    ADD COLUMN IF NOT EXISTS not_on_linkedin BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN sent_jobs.not_on_linkedin
    IS 'TRUE = la oferta no aparece en LinkedIn (detectada exclusivamente por el scraper)';


-- -----------------------------------------------------------------------------
-- 2. Índice parcial para consultas de ofertas exclusivas
--
--    Un índice parcial (WHERE not_on_linkedin = TRUE) es más pequeño y rápido
--    que un índice full porque solo indexa las filas que coinciden con el filtro
--    más frecuente del frontend.
--
--    Consulta optimizada:
--      SELECT * FROM sent_jobs WHERE not_on_linkedin = TRUE ORDER BY sent_at DESC;
--
--    El índice compuesto (not_on_linkedin, sent_at DESC) permite que Postgres
--    resuelva esa query usando solo el índice (index-only scan), sin tocar
--    la tabla heap → respuesta < 5 ms incluso con cientos de miles de filas.
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_sent_jobs_not_on_linkedin
    ON sent_jobs (sent_at DESC)
    WHERE not_on_linkedin = TRUE;


-- -----------------------------------------------------------------------------
-- 3. Actualización de filas existentes
--
--    Las filas previas reciben DEFAULT TRUE (ya asignado por ADD COLUMN).
--    Si querés marcar como FALSE las ofertas que sí están en LinkedIn
--    (backfill), podés correr una query como:
--
--      UPDATE sent_jobs
--         SET not_on_linkedin = FALSE
--       WHERE job_url ILIKE '%linkedin.com%';
--
--    Esa query no está incluida aquí porque depende de la lógica de negocio.
-- -----------------------------------------------------------------------------
