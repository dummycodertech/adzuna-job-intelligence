"""
export_csv.py — Adzuna Job Intelligence Pipeline
=================================================
Exports flat CSV files from the SQLite database for Power BI consumption.
This is the fallback path for users who cannot install the SQLite ODBC driver.

Outputs (written to data/exports/):
  postings_flat.csv  — one row per posting; skills as a pipe-delimited column
  skill_counts.csv   — aggregated skill frequency across all postings

Usage:
    python scripts/export_csv.py
"""

import csv
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "jobs.db"
EXPORTS_DIR = BASE_DIR / "data" / "exports"


def export_postings_flat(conn: sqlite3.Connection) -> int:
    """
    Export postings with their skills aggregated as a pipe-delimited string.
    Returns the number of rows written.
    """
    sql = """
        SELECT
            p.posting_id,
            p.title,
            p.company,
            p.location,
            p.salary_min,
            p.salary_max,
            p.currency,
            p.category,
            p.contract_type,
            p.created_date,
            p.pulled_at,
            p.title_bucket,
            p.city_query,
            p.redirect_url,
            GROUP_CONCAT(ps.skill, '|') AS skills
        FROM postings p
        LEFT JOIN posting_skills ps ON p.posting_id = ps.posting_id
        GROUP BY p.posting_id
        ORDER BY p.pulled_at DESC, p.posting_id
    """
    rows = conn.execute(sql).fetchall()
    out_path = EXPORTS_DIR / "postings_flat.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "posting_id", "title", "company", "location",
                "salary_min", "salary_max", "currency", "category",
                "contract_type", "created_date", "pulled_at",
                "title_bucket", "city_query", "redirect_url", "skills",
            ]
        )
        writer.writerows(rows)
    print(f"  [OK] postings_flat.csv — {len(rows)} rows")
    return len(rows)


def export_skill_counts(conn: sqlite3.Connection) -> int:
    """
    Export skill frequency table (count of distinct postings per skill).
    Returns the number of skill rows written.

    NOTE: These counts reflect skills found in Adzuna's truncated description
    snippets only. Skills mentioned deeper in full job listings are undercounted.
    """
    sql = """
        SELECT
            ps.skill,
            COUNT(DISTINCT ps.posting_id)  AS posting_count,
            SUM(CASE WHEN p.title_bucket = 'data analyst' THEN 1 ELSE 0 END)     AS data_analyst_count,
            SUM(CASE WHEN p.title_bucket = 'business analyst' THEN 1 ELSE 0 END) AS business_analyst_count
        FROM posting_skills ps
        JOIN postings p ON ps.posting_id = p.posting_id
        GROUP BY ps.skill
        ORDER BY posting_count DESC
    """
    rows = conn.execute(sql).fetchall()
    out_path = EXPORTS_DIR / "skill_counts.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["skill", "posting_count", "data_analyst_count", "business_analyst_count"]
        )
        writer.writerows(rows)
    print(f"  [OK] skill_counts.csv — {len(rows)} skill rows")
    return len(rows)


def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}. Run ingest.py first.")
        sys.exit(1)

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    print("\nExporting CSVs for Power BI...")
    n_postings = export_postings_flat(conn)
    n_skills = export_skill_counts(conn)
    conn.close()

    print(f"  Exports written to {EXPORTS_DIR}")
    print(f"  Total postings exported: {n_postings}")
    print(f"  Total skill types:       {n_skills}")


if __name__ == "__main__":
    main()
