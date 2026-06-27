-- =============================================================================
-- Migration: 004_target_companies_v2
-- Descripción: Reemplaza la tabla target_companies con schema mejorado.
--   - ats_type pasa de TEXT+CHECK a un ENUM tipado (ats_platform_enum)
--   - columna renombrada: active → is_active
--   - id usa gen_random_uuid() (nativo, sin depender de uuid-ossp)
--   - RLS: anon/authenticated pueden leer; solo service_role escribe
--
-- NOTA: este script hace DROP TABLE IF EXISTS target_companies CASCADE.
--   Si ya tenés datos en la tabla, exportalos antes de correr este script.
--   El seed al final re-inserta las empresas base incluidas en 003.
--
-- Cómo correrlo: SQL Editor de Supabase → pegar y ejecutar.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. ENUM  ats_platform_enum
--    Soporta los 13 ATS más comunes en startups tech.
--    Si el tipo ya existe se hace silenciosamente nada (EXCEPTION handling);
--    luego se agregan los valores nuevos con ADD VALUE IF NOT EXISTS para
--    garantizar que todos estén presentes aunque el tipo venga de una versión
--    anterior del schema.
-- -----------------------------------------------------------------------------

DO $$
BEGIN
    CREATE TYPE ats_platform_enum AS ENUM (
        'greenhouse',
        'lever',
        'ashby',
        'workable',
        'smartrecruiters',
        'breezy',
        'recruitee',
        'teamtailor',
        'pinpoint',
        'bamboohr',
        'homerun',
        'jobvite',
        'rippling'
    );
EXCEPTION
    WHEN duplicate_object THEN
        -- El tipo ya existe — los ALTER de abajo agregan los valores faltantes.
        NULL;
END;
$$;

-- Asegurar que todos los valores estén presentes si el ENUM preexistía.
-- ADD VALUE IF NOT EXISTS es idempotente y seguro de correr múltiples veces.
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'greenhouse';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'lever';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'ashby';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'workable';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'smartrecruiters';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'breezy';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'recruitee';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'teamtailor';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'pinpoint';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'bamboohr';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'homerun';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'jobvite';
ALTER TYPE ats_platform_enum ADD VALUE IF NOT EXISTS 'rippling';


-- -----------------------------------------------------------------------------
-- 2. TABLA  target_companies
--    Reemplaza la versión anterior (003_target_companies.sql).
--    CASCADE elimina también índices y políticas RLS que dependen de ella.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS target_companies CASCADE;

CREATE TABLE target_companies (
    id           UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT              NOT NULL,
    ats_type     ats_platform_enum NOT NULL,
    ats_id       TEXT              NOT NULL UNIQUE,
    is_active    BOOLEAN           NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  target_companies              IS 'Empresas objetivo para el motor directo de ATS (Greenhouse, Lever, etc.)';
COMMENT ON COLUMN target_companies.ats_id       IS 'Slug de la empresa en la URL pública del ATS (ej. "vercel", "vtex")';
COMMENT ON COLUMN target_companies.ats_type     IS 'Plataforma ATS que usa la empresa';
COMMENT ON COLUMN target_companies.is_active    IS 'FALSE desactiva la empresa sin borrarla';

-- Índice para el filtro más común en el pipeline: WHERE is_active = true
CREATE INDEX idx_target_companies_active
    ON target_companies (is_active);


-- -----------------------------------------------------------------------------
-- 3. ROW LEVEL SECURITY
--
--   Modelo de acceso:
--     • anon          → SELECT (lectura pública de empresas activas)
--     • authenticated → SELECT (mismo acceso que anon)
--     • service_role  → todo (INSERT, UPDATE, DELETE) — BYPASSRLS automático
--
--   El service_role de Supabase tiene el privilegio BYPASSRLS, por lo que
--   omite todas las políticas RLS y puede escribir sin restricciones.
--   No hace falta crear una política explícita para él.
-- -----------------------------------------------------------------------------

ALTER TABLE target_companies ENABLE ROW LEVEL SECURITY;

-- Lectura pública para clientes anónimos (frontend sin sesión)
CREATE POLICY "anon_select"
    ON target_companies
    FOR SELECT
    TO anon
    USING (is_active = TRUE);

-- Lectura para usuarios autenticados (mismos datos que anon)
CREATE POLICY "authenticated_select"
    ON target_companies
    FOR SELECT
    TO authenticated
    USING (is_active = TRUE);

-- Sin políticas de INSERT / UPDATE / DELETE para anon ni authenticated
-- → esas operaciones quedan implícitamente denegadas por RLS.
-- El service_role (usado en el backend Python) las ejecuta sin pasar por RLS.


-- -----------------------------------------------------------------------------
-- 4. SEED — empresas validadas contra las APIs (migradas desde 003)
--    ON CONFLICT DO NOTHING garantiza idempotencia: correr el script
--    dos veces no genera duplicados ni errores.
-- -----------------------------------------------------------------------------

INSERT INTO target_companies (company_name, ats_type, ats_id) VALUES
    -- Greenhouse
    ('Vercel',      'greenhouse', 'vercel'),
    ('Stripe',      'greenhouse', 'stripe'),
    ('Notion',      'greenhouse', 'notion'),
    ('Figma',       'greenhouse', 'figma'),
    ('Shopify',     'greenhouse', 'shopify'),
    ('Airbnb',      'greenhouse', 'airbnb'),
    -- Lever
    ('Netflix',     'lever',      'netflix'),
    ('Linear',      'lever',      'linear'),
    ('Loom',        'lever',      'loom'),
    ('Mercury',     'lever',      'mercury')
ON CONFLICT (ats_id) DO NOTHING;
