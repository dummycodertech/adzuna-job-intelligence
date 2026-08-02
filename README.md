# Adzuna Job Market Intelligence Tool

A live, scheduled data pipeline tracking skill demand and salary trends for
analyst-track roles across five Indian cities, built on the Adzuna Jobs API.

**Companion project** to the [CFPB dbt/DuckDB pipeline](../cfpb_complaints/README.md) —
different domain (recruitment, not consumer finance), different engine
(SQLite, not DuckDB), different ingestion pattern (scheduled REST API, not
bulk file load).

---

## Problem Statement

Which tools and skills appear most in Indian data/business analyst job postings?
How do salaries vary by city and role? Which companies are hiring most?

This pipeline answers those questions with real, weekly-updated data rather than
a one-time snapshot. The dataset grows automatically each week via GitHub Actions,
so trend claims are grounded in actual run history — not extrapolated from a
single pull.

---

## Architecture — Four Stages

```
┌─────────────────────────────────────────────────────────────┐
│  Stage 1 · Ingestion                                        │
│  scripts/ingest.py                                          │
│  - 10 API calls (2 titles × 5 cities), max 20 with paging  │
│  - Raw JSON saved → data/raw/run_YYYYMMDD.json              │
│  - Deduplication: INSERT OR IGNORE on Adzuna job ID (PK)    │
│  - Skill extraction via config/skills.yml regex patterns    │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│  Stage 2 · Storage                                          │
│  data/jobs.db (SQLite)                                      │
│  - postings table: one row per unique job posting           │
│  - posting_skills table: posting ↔ skill junction           │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│  Stage 3 · Quality Checks                                   │
│  scripts/validate.py → logs/validation_log.txt              │
│  - Duplicate PK check                                       │
│  - Salary min/max consistency check                         │
│  - Malformed record count from run log                      │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│  Stage 4 · Presentation                                     │
│  Power BI Desktop                                           │
│  - ODBC path: connect directly to data/jobs.db              │
│  - Fallback: data/exports/postings_flat.csv + skill_counts  │
│  - 4 visuals: skill frequency, salary by city,              │
│    postings by title bucket, top companies                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Query Plan & Quota Math

| Parameter | Value |
|-----------|-------|
| Title buckets | `"data analyst"`, `"business analyst"` |
| Cities | Delhi, Bangalore, Mumbai, Hyderabad, Pune |
| Results per page | 50 (API maximum) |
| Base calls per run | 10 (2 × 5) |
| Max calls per run | 20 (hard cap: 1 extra page per combination) |
| Run frequency | Weekly (Monday 02:00 UTC) |
| Estimated runs/month | ~4.3 |
| Estimated calls/month | ~43–86 |
| Free-tier budget | ~1,000/month |

**Why this sizing?** The free tier allows roughly 1,000 calls/month. Ten base
calls per run × 4.3 runs/month = 43 calls — well under 5% of the budget even
at the 20-call ceiling. The city and title list are deliberately fixed; adding
combinations requires re-checking this math first.

**Pagination hard cap:** If any combination returns more than 50 results, the
script fetches page 2 (1 extra call) and stops there. It logs a warning if the
API reports more than 100 total results, but does not fetch page 3+. The
`actual_api_calls` column in `logs/run_log.csv` makes quota consumption visible
after every run.

---

## Setup Instructions

### 1. Get Adzuna API Credentials

1. Register at https://developer.adzuna.com/ (free, no card required)
2. After registration, your `app_id` and `app_key` appear on the dashboard
3. Keep these private — they go in `.env` only, which is gitignored

### 2. Clone and Configure Locally

```powershell
# Clone the repo
git clone https://github.com/YOUR_USERNAME/adzuna-job-intelligence.git
cd adzuna-job-intelligence

# Run the setup script (creates venv, installs deps, copies .env)
.\setup.ps1

# Edit .env — fill in your actual credentials
notepad .env
```

The `.env` file looks like this (see `.env.example` for the template):
```
ADZUNA_APP_ID=abc12345
ADZUNA_APP_KEY=xyz98765abcdef...
```

### 3. Validate Wiring (no API calls)

```powershell
.venv\Scripts\python.exe scripts/ingest.py --dry-run
```

This checks: credentials present, skills YAML loads, schema applies, dirs exist.
Run this before your first live pull to confirm everything is wired correctly.

### 4. Run the Pipeline

```powershell
# Ingest
.venv\Scripts\python.exe scripts/ingest.py

# Validate
.venv\Scripts\python.exe scripts/validate.py

# Export CSVs for Power BI
.venv\Scripts\python.exe scripts/export_csv.py
```

### 5. GitHub Actions (Automated Weekly Runs)

1. Push this repo to GitHub (public repo required for free Actions minutes)
2. Add two repository secrets (**Settings → Secrets and variables → Actions**):
   - `ADZUNA_APP_ID` = your app_id
   - `ADZUNA_APP_KEY` = your app_key
3. The workflow (`.github/workflows/weekly_ingest.yml`) runs every Monday at
   02:00 UTC automatically
4. To trigger manually: **Actions → Weekly Job Data Ingestion → Run workflow**
5. After each run, the workflow commits `data/jobs.db`, `data/exports/`, and
   `logs/` back to the repo — the database visibly grows each week

---

## Power BI Setup

### Option A — Direct SQLite connection (preferred)

1. Install the free SQLite3 ODBC driver from https://www.ch-werner.de/sqliteodbc/
2. In Power BI Desktop: **Get Data → ODBC → SQLite3 Datasource**
3. Set the database path to the `data/jobs.db` file in your local clone
4. Build visuals directly against the `postings` and `posting_skills` tables

### Option B — CSV fallback

If the ODBC driver is unreliable:
1. In Power BI Desktop: **Get Data → Text/CSV**
2. Load `data/exports/postings_flat.csv` (one row per posting, skills pipe-delimited)
3. Load `data/exports/skill_counts.csv` (pre-aggregated skill frequencies)
4. Refresh by re-running `scripts/export_csv.py` and reloading in Power BI

### Suggested Visuals

| Visual | Table | Fields |
|--------|-------|--------|
| Skill demand (bar chart) | posting_skills | skill, COUNT(posting_id) |
| Salary range by city (box/bar) | postings | city_query, salary_min, salary_max |
| Postings by title bucket (donut) | postings | title_bucket, COUNT(posting_id) |
| Top companies hiring (bar) | postings | company, COUNT(posting_id) |

> **Dashboard screenshot**: *(to be added after Power BI visuals are built — requires credentials and a live run first)*

---

## Dedupe Verification

### Case 1 — Full overlap (day-one baseline)

The pipeline uses `INSERT OR IGNORE` with Adzuna's own job ID as the primary
key. To verify this is working:

```powershell
# Run 1 — first ingest
.venv\Scripts\python.exe scripts/ingest.py
# Note the "Total rows in postings" printed at the end → call this N₁

# Run 2 — immediate re-run, same data window
.venv\Scripts\python.exe scripts/ingest.py
# Row count must still be N₁, not 2×N₁
```

**Results (populate after running):**

| | Row count |
|--|-----------|
| After run 1 (N₁) | 905 |
| After run 2 (should = N₁) | 905 |
| New inserts in run 2 | 0 |

### Case 2 — Partial overlap (production-realistic)

After the first scheduled weekly run (run 2 in calendar time):

| | Row count |
|--|-----------|
| Before weekly run (N₁) | 905 |
| After weekly run (N₂) | 906 |
| New postings added | 1 |
| Duplicates re-inserted | 0 (confirmed by `skipped_duplicates` in run_log.csv) |

Case 1 proves `INSERT OR IGNORE` doesn't double-insert. Case 2 proves dedup
works under the actual production condition — partial overlap, where some
postings repeat and some are genuinely new. This is the test that maps to what
happens every week.

---

## Data Limitations & Honest Notes

### Skill frequency counts — snippet truncation

Adzuna's free-tier search endpoint returns a **truncated description snippet**,
not the full job listing text. Skill frequency counts therefore reflect only
skills mentioned within that snippet. Skills referenced deeper in the full
posting body (which requires clicking through to the employer's site) are
undercounted. This is an inherent API-tier constraint, not a pipeline bug.
The metric accurately measures "skill appears in Adzuna snippet for analyst
roles," not "skill is required for this role."

### Binary database in git history

`data/jobs.db` is committed to the repository so Power BI can connect to a
local file path and the dataset visibly grows each week. Binary database files
don't diff cleanly — a small insert can touch multiple B-tree pages, making each
commit larger than the actual new data. At ~50–200KB/run over a portfolio
timeline this is an accepted tradeoff. A production version would use git-lfs or
exclude the raw db from version control and reconstruct it from the committed
CSVs in `data/exports/`.

### Data maturity — no trend claims before ≥4 runs

A "trend over time" requires at least two data points. As of first run, there
is **one weekly run**, meaning the skill frequency chart shows current demand,
not a trend. The pipeline infrastructure for weekly growth is in place, but
trend language in presentations should wait until multiple runs have
accumulated. This note will be updated as runs complete.

**Runs completed as of last commit:** *(fill in)*

---

## File Structure

```
adzuna-job-intelligence/
├── .env.example              ← template: copy to .env, fill in credentials
├── .gitignore                ← .env and data/raw/ are gitignored
├── requirements.txt          ← requests, python-dotenv, PyYAML
├── setup.ps1                 ← Windows one-time setup script
├── README.md
│
├── config/
│   └── skills.yml            ← skill dictionary (30 terms, regex patterns)
│
├── scripts/
│   ├── schema.sql            ← SQLite DDL: postings + posting_skills tables
│   ├── ingest.py             ← ingestion: API calls, dedup, skill extraction
│   ├── validate.py           ← post-ingest quality checks
│   └── export_csv.py         ← flat CSV export for Power BI fallback
│
├── data/
│   ├── jobs.db               ← SQLite database (committed, grows weekly)
│   ├── exports/
│   │   ├── postings_flat.csv ← Power BI fallback flat file
│   │   └── skill_counts.csv  ← pre-aggregated skill frequencies
│   └── raw/                  ← gitignored; raw JSON per run (uploaded to
│                                GitHub Actions artifacts, 90-day retention)
│
├── logs/
│   ├── run_log.csv           ← one row per run: calls made, inserts, dupes
│   └── validation_log.txt    ← quality check results per run
│
└── .github/
    └── workflows/
        └── weekly_ingest.yml ← Monday 02:00 UTC cron + manual dispatch
```

---

## Deliverables Checklist

- [x] `.env.example` committed, `.env` gitignored, no credentials in repo
- [x] Ingestion script: fixed 10-combination query plan, hard pagination cap
- [x] SQLite schema with dedupe-safe `INSERT OR IGNORE` inserts
- [x] `config/skills.yml`: 30-term skill dictionary, separate from code
- [x] GitHub Actions workflow: weekly cron, repo secrets, `permissions: contents: write`
- [x] `logs/run_log.csv`: tracks `actual_api_calls` every run for quota visibility
- [x] Raw JSON uploaded to GitHub Actions artifacts (source fidelity on ephemeral runners)
- [x] Dedupe verification rows (fill in after first real run)
- [ ] Power BI dashboard — 4 visuals
- [ ] Dashboard screenshot
- [ ] Public GitHub repo URL

---

*Built as an "applied analyst" portfolio project alongside the CFPB dbt/DuckDB pipeline.
Different domain, different engine, different ingestion pattern — deliberately.*
