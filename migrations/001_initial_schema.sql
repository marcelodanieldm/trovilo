-- Migration: 001_initial_schema
-- Description: Creates search_filters and sent_jobs tables for the job scraper

-- Enable uuid-ossp extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Table: search_filters
-- Stores per-user search configuration sent to the scraper
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_filters (
    id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tech_stack     TEXT        NOT NULL,
    job_type       TEXT        NOT NULL,   -- e.g. 'remote', 'hybrid', 'on-site'
    location       TEXT        NOT NULL,
    telegram_user  TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Table: sent_jobs
-- Tracks job postings already delivered to users (deduplication)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sent_jobs (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_url     TEXT        NOT NULL UNIQUE,
    title       TEXT        NOT NULL,
    company     TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
