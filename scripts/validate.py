"""
validate.py — Adzuna Job Intelligence Pipeline
===============================================
Post-ingest data quality checks. Run automatically after ingest.py.
Appends results to logs/validation_log.txt.

Checks:
  1. Duplicate posting_id values in the postings table (should be 0)
  2. salary_min > salary_max violations (flagged, not silently dropped)
  3. Malformed/skipped record count from the most recent run log entry

Usage:
    python scripts/validate.py
"""

import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "jobs.db"
RUN_LOG_PATH = BASE_DIR / "logs" / "run_log.csv"
VALIDATION_LOG_PATH = BASE_DIR / "logs" / "validation_log.txt"


def get_latest_run_stats() -> dict | None:
    """Read the last row from run_log.csv for context in the validation output."""
    if not RUN_LOG_PATH.exists():
        return None
    with open(RUN_LOG_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


def run_checks(conn: sqlite3.Connection) -> tuple[list[str], bool]:
    """
    Execute all three quality checks.
    Returns (list_of_log_lines, passed: bool).
    """
    lines: list[str] = []
    all_passed = True

    # ------------------------------------------------------------------
    # Check 1: Duplicate posting_id values
    # ------------------------------------------------------------------
    dupe_rows = conn.execute(
        """
        SELECT posting_id, COUNT(*) AS cnt
        FROM postings
        GROUP BY posting_id
        HAVING cnt > 1
        """
    ).fetchall()

    if dupe_rows:
        all_passed = False
        lines.append(f"  [FAIL] Duplicate posting_ids found: {len(dupe_rows)} offenders")
        for row in dupe_rows[:10]:  # show first 10 to avoid log spam
            lines.append(f"         posting_id={row[0]}, count={row[1]}")
        if len(dupe_rows) > 10:
            lines.append(f"         ... and {len(dupe_rows) - 10} more")
    else:
        lines.append("  [PASS] No duplicate posting_ids")

    # ------------------------------------------------------------------
    # Check 2: salary_min > salary_max violations
    # ------------------------------------------------------------------
    salary_violations = conn.execute(
        """
        SELECT posting_id, salary_min, salary_max
        FROM postings
        WHERE salary_min IS NOT NULL
          AND salary_max IS NOT NULL
          AND salary_min > salary_max
        """
    ).fetchall()

    if salary_violations:
        # Log violations but do NOT drop them — flagging only, per spec
        lines.append(
            f"  [WARN] salary_min > salary_max violations: {len(salary_violations)} rows"
        )
        for row in salary_violations[:10]:
            lines.append(
                f"         posting_id={row[0]}, salary_min={row[1]}, salary_max={row[2]}"
            )
        if len(salary_violations) > 10:
            lines.append(f"         ... and {len(salary_violations) - 10} more")
    else:
        lines.append("  [PASS] No salary_min > salary_max violations")

    # ------------------------------------------------------------------
    # Check 3: Malformed/skipped count from most recent run
    # ------------------------------------------------------------------
    latest = get_latest_run_stats()
    if latest:
        malformed = int(latest.get("malformed_skipped", 0))
        api_calls = latest.get("actual_api_calls", "?")
        pag_warnings = latest.get("pagination_warnings", "0")
        if malformed > 0:
            lines.append(f"  [WARN] Malformed/skipped records in latest run: {malformed}")
        else:
            lines.append(f"  [PASS] No malformed/skipped records in latest run")
        lines.append(f"  [INFO] Latest run: {api_calls} actual API calls, {pag_warnings} pagination cap warnings")
    else:
        lines.append("  [INFO] No run_log.csv found — skipping malformed check")

    # ------------------------------------------------------------------
    # Summary row counts
    # ------------------------------------------------------------------
    total_postings = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    total_skills = conn.execute("SELECT COUNT(*) FROM posting_skills").fetchone()[0]
    distinct_skills = conn.execute(
        "SELECT COUNT(DISTINCT skill) FROM posting_skills"
    ).fetchone()[0]
    lines.append(
        f"  [INFO] Table counts — postings: {total_postings}, "
        f"posting_skills rows: {total_skills}, distinct skills: {distinct_skills}"
    )

    return lines, all_passed


def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}. Run ingest.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    check_lines, all_passed = run_checks(conn)
    conn.close()

    # Write to validation log
    ts = datetime.now(timezone.utc).isoformat()
    header = f"\n{'='*60}\nValidation run: {ts}\n{'='*60}"
    footer = f"  Result: {'ALL CHECKS PASSED' if all_passed else 'ONE OR MORE CHECKS FAILED'}\n"

    log_block = "\n".join([header] + check_lines + [footer])

    with open(VALIDATION_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(log_block + "\n")

    # Also print to stdout (visible in CI logs)
    print(log_block)

    if not all_passed:
        sys.exit(1)  # Non-zero exit code flags the CI step as failed


if __name__ == "__main__":
    main()
