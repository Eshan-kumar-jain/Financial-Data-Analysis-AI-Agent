"""Export the evaluation tables from Postgres to Parquet for the Streamlit app.

Postgres stays the single source of truth. These Parquet files are a
read-only DEPLOYMENT SNAPSHOT for Streamlit Community Cloud, which has no
database - they are regenerated from Postgres by this script, never edited
and never read back into the pipeline.

LABEL WARNING (same as sql/03_eval_outputs.sql): eval_entry.is_error,
eval_entry_label and every outcome column derive from ground_truth. These
are reporting tables for showing how the detectors did - never a model input.

Deliberately NOT exported:
  - eval_method_score: the stored scoreboard. streamlit_app/metrics.py
    recomputes every figure from the base rows, so the app can't quietly
    report a stored number that has drifted (the per-slice double-count bug
    lived in exactly that table).
  - employee names: posters are pseudonymised here, at export, so a real
    name never reaches the Parquet files or the deployed app.

Usage (repo root, .env populated):
  .venv/Scripts/python.exe scripts/export_parquet.py
"""
import os
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "streamlit_app" / "data"
SIZE_WARN_MB = 50

load_dotenv(ROOT / ".env")
engine = create_engine(
    f"postgresql+psycopg2://{quote_plus(os.environ['PGUSER'])}:{quote_plus(os.environ['PGPASSWORD'])}"
    f"@{os.environ['PGHOST']}:{os.environ['PGPORT']}/{os.environ['PGDATABASE']}"
)

QUERIES = {
    # Method dimension: family, scoreboard, threshold, pipeline sort order.
    "eval_method": "SELECT * FROM eval_method ORDER BY sort_order",

    # One row per test-period header - the evaluation population.
    "eval_entry": """
        SELECT header_id, fiscal_period, date_key, employee_key, total_amount,
               is_error, n_labels, final_flag, final_outcome
        FROM eval_entry
        ORDER BY header_id
    """,

    # Pair grain: one row per (header, error_type). Per-type recall counts these.
    "eval_entry_label": """
        SELECT header_id, error_type, detectability
        FROM eval_entry_label
        ORDER BY header_id, error_type
    """,

    # Long fact: every method's verdict on every header (16 x 28,695).
    "eval_entry_flag": """
        SELECT header_id, method_name, is_flagged, is_covered, score, outcome
        FROM eval_entry_flag
        ORDER BY method_name, header_id
    """,

    # XGBoost cost curve on the 0.01-0.99 grid, is_chosen marks 0.57.
    "eval_threshold_sweep": "SELECT * FROM eval_threshold_sweep ORDER BY threshold",

    # Posters, pseudonymised. ROW_NUMBER() OVER (ORDER BY employee_key) gives
    # each employee a stable 'Poster NN' label: ordered by the surrogate key,
    # not by volume, so the label doesn't change when posting counts change
    # and doesn't encode rank. employee_name / employee_id are not selected.
    "poster": """
        SELECT employee_key,
               'Poster ' || LPAD(ROW_NUMBER() OVER (ORDER BY employee_key)::text, 2, '0') AS poster,
               department,
               role,
               seniority_level
        FROM dim_employee
        ORDER BY employee_key
    """,
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    print(f"{'file':28} {'rows':>9} {'cols':>5} {'size':>10}")
    with engine.connect() as conn:
        for name, sql in QUERIES.items():
            df = pd.read_sql(sql, conn)
            path = OUT / f"{name}.parquet"
            df.to_parquet(path, index=False, compression="zstd")
            size = path.stat().st_size
            total += size
            flag = "  <-- over limit, pre-aggregate" if size > SIZE_WARN_MB * 1024 ** 2 else ""
            print(f"{path.name:28} {len(df):>9,} {df.shape[1]:>5} {size / 1024:>8,.1f} KB{flag}")
    print(f"{'total':28} {'':>9} {'':>5} {total / 1024:>8,.1f} KB")


if __name__ == "__main__":
    main()
