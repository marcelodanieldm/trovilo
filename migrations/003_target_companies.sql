-- Migration: 003_target_companies
-- Descripción: Tabla de empresas objetivo para el motor directo de ATS.
--   En vez de buscar en Google/DDG, el motor consulta directamente los
--   endpoints JSON públicos de Greenhouse y Lever por cada empresa.
--
-- Uso:
--   INSERT INTO target_companies (company_name, ats_type, ats_id)
--   VALUES ('Vercel', 'greenhouse', 'vercel');

CREATE TABLE IF NOT EXISTS target_companies (
    id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_name TEXT        NOT NULL,
    ats_type     TEXT        NOT NULL
                             CHECK (ats_type IN ('greenhouse', 'lever')),
    ats_id       TEXT        NOT NULL UNIQUE,   -- slug que va en la URL del API
    active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice para filtrar rápidamente las filas activas en cada run
CREATE INDEX IF NOT EXISTS idx_target_companies_active
    ON target_companies (active);

-- Seed: empresas conocidas con boards públicos (validadas contra las APIs)
INSERT INTO target_companies (company_name, ats_type, ats_id) VALUES
    ('Vercel',      'greenhouse', 'vercel'),
    ('Stripe',      'greenhouse', 'stripe'),
    ('Notion',      'greenhouse', 'notion'),
    ('Figma',       'greenhouse', 'figma'),
    ('Shopify',     'greenhouse', 'shopify'),
    ('Airbnb',      'greenhouse', 'airbnb'),
    ('Netflix',     'lever',      'netflix'),
    ('Linear',      'lever',      'linear'),
    ('Loom',        'lever',      'loom'),
    ('Mercury',     'lever',      'mercury')
ON CONFLICT (ats_id) DO NOTHING;
