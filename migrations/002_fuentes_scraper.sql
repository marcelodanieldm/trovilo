-- Migration: 002_fuentes_scraper
-- Description: Creates fuentes_scraper table to store custom job board domains

CREATE TABLE IF NOT EXISTS fuentes_scraper (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain     TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
