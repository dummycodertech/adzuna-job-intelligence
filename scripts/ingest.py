"""
ingest.py — Adzuna Job Intelligence Pipeline
============================================
Ingests Indian job postings from the Adzuna search API for the fixed
10-combination query plan (2 title buckets × 5 cities), writes raw JSON
to data/raw/ for source fidelity, deduplicates on Adzuna's job ID,
extracts skills from the skill dictionary, and logs run metrics.

Usage:
    python scripts/ingest.py           # live run
    python scripts/ingest.py --dry-run # validate wiring without hitting API

Quota budget:
    Base:     10 calls/run  (1 per combination)
    Max:      20 calls/run  (hard cap: 1 extra page if count > 50)
    Monthly:  ~43 calls     (weekly cron, 4.3 runs/month)
    Free tier: ~1,000/month
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "jobs.db"
SCHEMA_PATH = BASE_DIR / "scripts" / "schema.sql"
SKILLS_PATH = BASE_DIR / "config" / "skills.yml"
RUN_LOG_PATH = LOGS_DIR / "run_log.csv"

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"
RESULTS_PER_PAGE = 50

# Fixed query plan — do not expand without re-checking monthly quota math.
# 2 titles × 5 cities = 10 base API calls per run.
TITLE_BUCKETS = ["data analyst", "business analyst"]
CITIES = ["Delhi", "Bangalore", "Mumbai", "Hyderabad", "Pune"]

# Pagination hard cap: fetch at most 1 extra page per combination.
# Worst case: 20 calls/run (10 combos × 2 pages).
# Any combo returning >100 results logs a warning but stops at page 2.
MAX_EXTRA_PAGES = 1

RUN_LOG_FIELDS = [
    "run_timestamp",
    "combinations_queried",
    "postings_fetched",
    "new_inserts",
    "skipped_duplicates",
    "malformed_skipped",
    "actual_api_calls",
    "pagination_warnings",   # combos where count > 100 (cap hit)
]

# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def ensure_dirs() -> None:
    """Create required directories if they don't exist."""
    for d in (DATA_DIR, RAW_DIR, LOGS_DIR, DATA_DIR / "exports"):
        d.mkdir(parents=True, exist_ok=True)


def load_credentials() -> tuple[str, str]:
    """Load app_id/app_key from environment (populated from .env or CI secrets)."""
    load_dotenv()
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        sys.exit(
            "ERROR: ADZUNA_APP_ID and ADZUNA_APP_KEY must be set.\n"
            "  Local: copy .env.example → .env and fill in your credentials.\n"
            "  CI:    add ADZUNA_APP_ID and ADZUNA_APP_KEY as repository secrets."
        )
    return app_id, app_key


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql to the database (safe to call on every run)."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def load_skills() -> list[dict]:
    """Load and compile skill patterns from config/skills.yml."""
    with open(SKILLS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    skills = []
    for entry in data["skills"]:
        skills.append(
            {
                "name": entry["name"],
                "regex": re.compile(entry["pattern"], re.IGNORECASE),
            }
        )
    return skills


def ensure_run_log() -> None:
    """Create run_log.csv with header row if it doesn't exist."""
    if not RUN_LOG_PATH.exists():
        with open(RUN_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RUN_LOG_FIELDS)
            writer.writeheader()


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


def fetch_page(
    app_id: str,
    app_key: str,
    title: str,
    city: str,
    page: int,
) -> dict:
    """
    Make a single Adzuna search API call.
    Returns the parsed JSON response dict.
    Raises requests.HTTPError on non-200 responses.
    """
    url = ADZUNA_BASE.format(page=page)
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": title,
        "where": city,
        "results_per_page": RESULTS_PER_PAGE,
        "content-type": "application/json",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_combination(
    app_id: str,
    app_key: str,
    title: str,
    city: str,
) -> tuple[list[dict], int, bool]:
    """
    Fetch all results for one (title, city) combination.
    Returns (results_list, api_calls_made, pagination_warning_hit).

    Pagination hard cap: maximum 1 extra page (page 2) per combination.
    If total_count > 100 (i.e., more pages exist beyond page 2), logs a warning
    but does NOT fetch further — preserving the 20-call/run budget ceiling.
    """
    results: list[dict] = []
    api_calls = 0
    pagination_warning = False

    # Page 1 (always fetched)
    data = fetch_page(app_id, app_key, title, city, page=1)
    api_calls += 1
    total_count = data.get("count", 0)
    results.extend(data.get("results", []))

    # Optionally fetch page 2 if more results exist
    if total_count > RESULTS_PER_PAGE:
        data2 = fetch_page(app_id, app_key, title, city, page=2)
        api_calls += 1
        results.extend(data2.get("results", []))

        # Check if we're hitting the cap and data is still being left on the table
        if total_count > RESULTS_PER_PAGE * (1 + MAX_EXTRA_PAGES):
            pagination_warning = True
            print(
                f"  [WARN] Pagination cap hit for '{title}' / {city}: "
                f"{total_count} total results, fetched {len(results)} "
                f"(stopped at page 2 to protect quota)."
            )

    return results, api_calls, pagination_warning


# ---------------------------------------------------------------------------
# Parsing & storage
# ---------------------------------------------------------------------------


def parse_posting(raw: dict, title_bucket: str, city_query: str, pulled_at: str) -> dict | None:
    """
    Extract fields from a raw Adzuna job posting dict.
    Returns a clean dict ready for INSERT, or None if required fields are missing.
    """
    posting_id = raw.get("id")
    title = raw.get("title")
    if not posting_id or not title:
        return None  # Required fields missing — will be counted as malformed

    salary_data = raw.get("salary_min"), raw.get("salary_max")

    return {
        "posting_id": str(posting_id),
        "title": title,
        "company": (raw.get("company") or {}).get("display_name"),
        "location": (raw.get("location") or {}).get("display_name"),
        "salary_min": salary_data[0],
        "salary_max": salary_data[1],
        "currency": "INR",
        "category": (raw.get("category") or {}).get("label"),
        "contract_type": raw.get("contract_type"),
        "created_date": raw.get("created"),
        "pulled_at": pulled_at,
        "description": raw.get("description"),
        "redirect_url": raw.get("redirect_url"),
        "title_bucket": title_bucket,
        "city_query": city_query,
    }


def extract_skills(posting: dict, skills: list[dict]) -> list[str]:
    """
    Regex-match skill patterns against title + description text.
    Returns list of matched canonical skill names.

    NOTE: Adzuna's free-tier search endpoint returns a TRUNCATED description
    snippet, not the full job listing text. Skill counts therefore reflect only
    what appears in that snippet — skills mentioned deeper in the full posting
    are undercounted. This is an inherent API-tier limitation, not a pipeline bug.
    """
    text = " ".join(
        filter(None, [posting.get("title") or "", posting.get("description") or ""])
    )
    return [s["name"] for s in skills if s["regex"].search(text)]


def insert_posting(conn: sqlite3.Connection, posting: dict) -> bool:
    """
    Insert a posting using INSERT OR IGNORE.
    Returns True if a new row was inserted, False if it was a duplicate.
    """
    sql = """
        INSERT OR IGNORE INTO postings
            (posting_id, title, company, location,
             salary_min, salary_max, currency, category,
             contract_type, created_date, pulled_at,
             description, redirect_url, title_bucket, city_query)
        VALUES
            (:posting_id, :title, :company, :location,
             :salary_min, :salary_max, :currency, :category,
             :contract_type, :created_date, :pulled_at,
             :description, :redirect_url, :title_bucket, :city_query)
    """
    cursor = conn.execute(sql, posting)
    return cursor.rowcount == 1  # 1 = inserted, 0 = ignored (duplicate)


def insert_skills(conn: sqlite3.Connection, posting_id: str, skill_names: list[str]) -> None:
    """Insert skill associations using INSERT OR IGNORE (idempotent)."""
    for skill in skill_names:
        conn.execute(
            "INSERT OR IGNORE INTO posting_skills (posting_id, skill) VALUES (?, ?)",
            (posting_id, skill),
        )


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


def dry_run() -> None:
    """
    Validate pipeline wiring without making any API calls.
    Checks: credentials present, schema applies, skills load, dirs exist.
    """
    print("=== DRY RUN — no API calls will be made ===\n")
    ensure_dirs()
    print(f"  [OK] Directories: {DATA_DIR}, {RAW_DIR}, {LOGS_DIR}")

    # Credentials check (existence, not validity)
    load_dotenv()
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if app_id and app_key:
        print(f"  [OK] Credentials found (app_id length={len(app_id)})")
    else:
        print("  [WARN] ADZUNA_APP_ID or ADZUNA_APP_KEY not set — expected for dry run")

    # Skills
    skills = load_skills()
    print(f"  [OK] Loaded {len(skills)} skill patterns from {SKILLS_PATH.name}")

    # Schema
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    print(f"  [OK] Schema applied: tables = {[t[0] for t in tables]}")
    conn.close()

    # Run log
    ensure_run_log()
    print(f"  [OK] Run log at {RUN_LOG_PATH}")

    # Query plan summary
    print(f"\n  Query plan: {len(TITLE_BUCKETS)} title buckets × {len(CITIES)} cities")
    print(f"             = {len(TITLE_BUCKETS) * len(CITIES)} base API calls/run")
    print(f"             = {len(TITLE_BUCKETS) * len(CITIES) * (1 + MAX_EXTRA_PAGES)} max calls/run (with pagination cap)")
    print("\n=== Dry run complete — all checks passed ===")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Adzuna ingestion script")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate wiring without hitting the API",
    )
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    # --- Setup ---
    ensure_dirs()
    app_id, app_key = load_credentials()
    skills = load_skills()
    ensure_run_log()

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pulled_at = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*60}")
    print(f"  Adzuna Ingestion Run: {run_ts}")
    print(f"  Query plan: {len(TITLE_BUCKETS)} titles × {len(CITIES)} cities")
    print(f"{'='*60}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    # Counters
    total_fetched = 0
    total_new = 0
    total_dupes = 0
    total_malformed = 0
    total_api_calls = 0
    total_pagination_warnings = 0
    all_raw_results: list[dict] = []

    # --- Ingest loop ---
    for title in TITLE_BUCKETS:
        for city in CITIES:
            print(f"  Querying: '{title}' / {city} ... ", end="", flush=True)
            try:
                results, api_calls, pag_warn = fetch_combination(
                    app_id, app_key, title, city
                )
            except requests.HTTPError as e:
                print(f"HTTP ERROR: {e}")
                total_malformed += 1  # count the failed combination
                continue
            except requests.RequestException as e:
                print(f"REQUEST ERROR: {e}")
                total_malformed += 1
                continue

            total_api_calls += api_calls
            if pag_warn:
                total_pagination_warnings += 1

            # Tag raw results with query context before saving
            for r in results:
                r["_title_bucket"] = title
                r["_city_query"] = city
            all_raw_results.extend(results)

            inserted_this_combo = 0
            dupes_this_combo = 0
            malformed_this_combo = 0

            for raw in results:
                posting = parse_posting(raw, title, city, pulled_at)
                if posting is None:
                    malformed_this_combo += 1
                    total_malformed += 1
                    continue

                is_new = insert_posting(conn, posting)
                if is_new:
                    matched_skills = extract_skills(posting, skills)
                    insert_skills(conn, posting["posting_id"], matched_skills)
                    inserted_this_combo += 1
                    total_new += 1
                else:
                    dupes_this_combo += 1
                    total_dupes += 1

            total_fetched += len(results)
            conn.commit()
            print(
                f"{len(results)} fetched, {inserted_this_combo} new, "
                f"{dupes_this_combo} dupes, {malformed_this_combo} malformed"
                f"{' [PAGE CAP HIT]' if pag_warn else ''}"
            )

    # --- Save raw JSON (source fidelity) ---
    raw_path = RAW_DIR / f"run_{run_ts}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_timestamp": pulled_at,
                "combinations": [
                    {"title": t, "city": c}
                    for t in TITLE_BUCKETS
                    for c in CITIES
                ],
                "results": all_raw_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n  Raw JSON saved -> {raw_path.name}")

    # --- Row count (for dedupe verification) ---
    row_count_after = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    conn.close()

    # --- Log run metrics ---
    with open(RUN_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_LOG_FIELDS)
        writer.writerow(
            {
                "run_timestamp": pulled_at,
                "combinations_queried": len(TITLE_BUCKETS) * len(CITIES),
                "postings_fetched": total_fetched,
                "new_inserts": total_new,
                "skipped_duplicates": total_dupes,
                "malformed_skipped": total_malformed,
                "actual_api_calls": total_api_calls,
                "pagination_warnings": total_pagination_warnings,
            }
        )

    # --- Summary ---
    print(f"\n{'='*60}")
    print("  Run Summary")
    print(f"{'='*60}")
    print(f"  Postings fetched:        {total_fetched}")
    print(f"  New inserts:             {total_new}")
    print(f"  Skipped (duplicates):    {total_dupes}")
    print(f"  Skipped (malformed):     {total_malformed}")
    print(f"  Actual API calls made:   {total_api_calls}  (budget: 10 base, 20 max)")
    print(f"  Pagination warnings:     {total_pagination_warnings}")
    print(f"  Total rows in postings:  {row_count_after}  <- use for dedupe verification")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
