-- SQLite schema for the Adzuna Job Intelligence Pipeline
-- Run once to initialise, safe to run repeatedly (IF NOT EXISTS guards).

-- Primary postings table.
-- posting_id is Adzuna's own job ID — used as PK for deduplication.
-- INSERT OR IGNORE on this PK prevents duplicate rows across weekly runs.
CREATE TABLE IF NOT EXISTS postings (
    posting_id    TEXT PRIMARY KEY,
    title         TEXT,
    company       TEXT,
    location      TEXT,
    salary_min    REAL,
    salary_max    REAL,
    currency      TEXT DEFAULT 'INR',
    category      TEXT,
    contract_type TEXT,
    created_date  TEXT,   -- ISO-8601 string as returned by Adzuna
    pulled_at     TEXT,   -- ISO-8601 UTC timestamp of THIS ingestion run
    description   TEXT,   -- Truncated snippet as returned by Adzuna free tier
    redirect_url  TEXT,
    title_bucket  TEXT,   -- 'data analyst' | 'business analyst'
    city_query    TEXT    -- Query city: Delhi | Bangalore | Mumbai | Hyderabad | Pune
);

-- Junction table: one row per (posting, skill) pair.
-- Composite PK makes INSERT OR IGNORE idempotent — safe to re-run extraction.
CREATE TABLE IF NOT EXISTS posting_skills (
    posting_id  TEXT NOT NULL REFERENCES postings(posting_id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    PRIMARY KEY (posting_id, skill)
);

-- Index to speed up skill-frequency GROUP BY queries (used by Power BI views).
CREATE INDEX IF NOT EXISTS idx_ps_skill ON posting_skills(skill);

-- Index on pulled_at for weekly trend queries once multiple runs exist.
CREATE INDEX IF NOT EXISTS idx_postings_pulled_at ON postings(pulled_at);

-- Index on city/title bucket for the salary-by-city and postings-by-title views.
CREATE INDEX IF NOT EXISTS idx_postings_city ON postings(city_query);
CREATE INDEX IF NOT EXISTS idx_postings_bucket ON postings(title_bucket);
