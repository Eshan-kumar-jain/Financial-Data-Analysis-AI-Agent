"""Validate the Phase 6 DAX measures against SQL recomputed from the eval_* tables.

This is the artefact behind the "every DAX measure validated against SQL" rule in
CLAUDE.md. Three sources are compared:

  1. DAX  - results of the measures in powerbi/anomaly_detection.pbix, run through
            the powerbi-modeling-mcp server (dax_query_operations -> Execute) and
            pasted into the DAX_* constants below. Python cannot query the Desktop
            model directly, so after a model or data change, re-run the DAX queries
            and update the constants. Last captured 2026-09-30, against the model
            refreshed after the eval_method_score per-slice fix (unchanged from
            the pre-refresh capture - the measures read eval_entry_flag /
            eval_entry_label, not the stored scoreboard).
  2. SQL  - every figure recomputed from the base rows (eval_entry_flag,
            eval_entry_label, eval_threshold_sweep). This is the reference.
  3. Stored - eval_method_score as written by notebooks/05_evaluation.ipynb
            Section 10, read as an independent third source.

DAX queries used (measures live in the model's Header-level / Pair-level /
Operating point display folders):
  header:  EVALUATE SUMMARIZECOLUMNS(eval_method[method_name], "flags", [Flags],
             "tp", [True Positives], "fp", [False Positives], "fn", [False Negatives])
  pairs:   EVALUATE SUMMARIZECOLUMNS(eval_method[method_name],
             eval_entry_label[error_type], "pairs", [Label Pairs], "caught", [Pairs Caught])
  tiers:   same as pairs on eval_entry_label[detectability], filtered to
             eval_method[method_name] = "Final layered system"
  op:      EVALUATE ROW("thr", [Chosen Threshold], "rec", [Chosen Recall],
             "prec", [Chosen Precision], "hrs", [Chosen Analyst Hours per Month])

Exit code is non-zero on any mismatch.

Usage (from the repo root, with .env populated):
  python scripts/validate_dax.py
"""
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
engine = create_engine(
    f"postgresql+psycopg2://{quote_plus(os.environ['PGUSER'])}:{quote_plus(os.environ['PGPASSWORD'])}"
    f"@{os.environ['PGHOST']}:{os.environ['PGPORT']}/{os.environ['PGDATABASE']}"
)

TYPES = ["unmatched_bank", "round_number", "off_hours_posting", "structuring",
         "unusual_account_pair", "duplicate", "unbalanced", "backdated"]

# --- DAX results: header-level (flags, tp, fp, fn) --------------------------
DAX_HEADER = {
    "Benford (account MAD)":            (2064, 119, 1945, 1174),
    "Segmented z-score":                (308, 143, 165, 1150),
    "Segmented IQR":                    (1539, 381, 1158, 912),
    "Isolation Forest":                 (1814, 317, 1497, 976),
    "Reconciliation":                   (83, 83, 0, 1210),
    "Logistic Regression":              (5210, 898, 4312, 395),
    "Random Forest":                    (957, 716, 241, 577),
    "XGBoost":                          (1824, 803, 1021, 490),
    "L1 balance check (SQL aggregate)": (76, 76, 0, 1217),
    "L2 duplicate self-join (SQL)":     (191, 142, 49, 1151),
    "L3 reconciliation (blocking join)":(83, 83, 0, 1210),
    "L4 XGBoost @ 0.57":                (1377, 789, 588, 504),
    "Unsupervised rule union":          (4986, 652, 4334, 641),
    "Supervised soft-vote @ 0.50":      (1777, 805, 972, 488),
    "XGBoost OR unsupervised union":    (5768, 968, 4800, 325),
    "Final layered system":             (1710, 1073, 637, 220),
}

# --- DAX results: Pairs Caught per method x error_type (TYPES order) --------
DAX_PAIRS = {
    "Benford (account MAD)":            [6, 21, 12, 34, 15, 15, 5, 12],
    "Segmented z-score":                [0, 97, 1, 42, 0, 1, 0, 2],
    "Segmented IQR":                    [0, 164, 2, 188, 5, 9, 1, 12],
    "Isolation Forest":                 [8, 214, 8, 46, 3, 5, 1, 32],
    "Reconciliation":                   [81, 1, 1, 2, 1, 0, 1, 0],
    "Logistic Regression":              [14, 274, 148, 211, 122, 29, 24, 79],
    "Random Forest":                    [3, 274, 109, 166, 133, 3, 6, 24],
    "XGBoost":                          [5, 274, 123, 205, 134, 6, 7, 52],
    "L1 balance check (SQL aggregate)": [0, 0, 0, 0, 0, 0, 76, 0],
    "L2 duplicate self-join (SQL)":     [0, 4, 0, 0, 0, 138, 0, 0],
    "L3 reconciliation (blocking join)":[81, 1, 1, 2, 1, 0, 1, 0],
    "L4 XGBoost @ 0.57":                [3, 274, 122, 203, 134, 4, 4, 47],
    "Unsupervised rule union":          [81, 223, 22, 223, 22, 27, 7, 51],
    "Supervised soft-vote @ 0.50":      [5, 274, 129, 197, 135, 7, 5, 55],
    "XGBoost OR unsupervised union":    [81, 274, 127, 229, 136, 28, 13, 84],
    "Final layered system":             [81, 274, 122, 205, 134, 138, 76, 47],
}
DAX_PAIRS_DENOM = [82, 274, 154, 242, 150, 138, 76, 181]
DAX_TIER_FINAL = {"easy": (420, 415), "medium": (330, 290), "hard": (547, 372)}
DAX_OP = (0.57, 0.6102088167053364, 0.5729847494553377, 8.166666666666666)

# --- SQL -------------------------------------------------------------------
SQL_HEADER = """
-- Header-level confusion counts per method, straight off the long flag fact.
SELECT method_name,
       COUNT(*) FILTER (WHERE is_flagged)     AS flags,
       COUNT(*) FILTER (WHERE outcome = 'TP') AS tp,
       COUNT(*) FILTER (WHERE outcome = 'FP') AS fp,
       COUNT(*) FILTER (WHERE outcome = 'FN') AS fn,
       COUNT(*)                               AS n
FROM eval_entry_flag
GROUP BY method_name
"""

SQL_PAIRS = """
-- recall_check_rs generalised to every method: pair grain from
-- eval_entry_label, joined to that method's flag on header_id.
SELECT f.method_name, l.error_type,
       COUNT(*)                             AS n_pairs,
       COUNT(*) FILTER (WHERE f.is_flagged) AS caught
FROM eval_entry_label l
JOIN eval_entry_flag  f ON f.header_id = l.header_id
GROUP BY f.method_name, l.error_type
"""

SQL_TIER = """
SELECT l.detectability,
       COUNT(*)                             AS n_pairs,
       COUNT(*) FILTER (WHERE f.is_flagged) AS caught
FROM eval_entry_label l
JOIN eval_entry_flag  f ON f.header_id = l.header_id
                       AND f.method_name = 'Final layered system'
GROUP BY l.detectability
"""

SQL_OP = """
SELECT threshold, recall, precision, analyst_hours_per_month
FROM eval_threshold_sweep WHERE is_chosen
"""

SQL_STORED = """
-- Third source: what Section 10 wrote to the scoreboard table.
SELECT method_name, slice_type, slice_value, precision, recall,
       precision_vs_slice, analyst_hours_per_month
FROM eval_method_score
WHERE slice_type IN ('overall', 'error_type')
"""

with engine.connect() as c:
    hdr = {r.method_name: r for r in c.execute(text(SQL_HEADER))}
    pairs = {(r.method_name, r.error_type): r for r in c.execute(text(SQL_PAIRS))}
    tier = {r.detectability: r for r in c.execute(text(SQL_TIER))}
    op = c.execute(text(SQL_OP)).one()
    stored = {(r.method_name, r.slice_type, r.slice_value): r for r in c.execute(text(SQL_STORED))}

mismatches = []
close = lambda a, b, tol=1e-9: a is not None and b is not None and abs(float(a) - float(b)) <= tol

print("=== 1. Header-level: DAX vs SQL recompute vs stored eval_method_score ===")
print(f"{'method':36} {'flags':>6} {'TP':>5} {'FP':>5} {'FN':>5} {'prec':>6} {'rec':>6} {'hrs/mo':>6}  counts  vs-stored")
for m, (fl, tp, fp, fn) in DAX_HEADER.items():
    s = hdr[m]
    counts_ok = (fl, tp, fp, fn) == (s.flags, s.tp, s.fp, s.fn)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn)
    hrs = fp * 5 / 60 / 6
    st = stored[(m, "overall", "all")]
    stored_ok = (round(prec, 3) == round(float(st.precision), 3)
                 and round(rec, 3) == round(float(st.recall), 3)
                 and (st.analyst_hours_per_month is None or round(hrs, 2) == round(float(st.analyst_hours_per_month), 2)))
    if not counts_ok:
        mismatches.append(("header", m, (fl, tp, fp, fn), (s.flags, s.tp, s.fp, s.fn)))
    if not stored_ok:
        mismatches.append(("header-stored", m, (prec, rec, hrs), (st.precision, st.recall, st.analyst_hours_per_month)))
    print(f"{m:36} {fl:>6} {tp:>5} {fp:>5} {fn:>5} {prec:6.3f} {rec:6.3f} {hrs:6.1f}  {'MATCH' if counts_ok else 'MISMATCH':6}  {'MATCH' if stored_ok else 'MISMATCH'}")
    assert s.n == 28695, m

print("\n=== 2. Pair-level Pairs Caught / Pair Recall: 16 methods x 8 types ===")
n_cells = n_ok = 0
for m, caught_list in DAX_PAIRS.items():
    for t, dax_caught, dax_n in zip(TYPES, caught_list, DAX_PAIRS_DENOM):
        s = pairs[(m, t)]
        st = stored[(m, "error_type", t)]
        n_cells += 1
        ok = (dax_caught == s.caught and dax_n == s.n_pairs
              and round(dax_caught / dax_n, 3) == round(float(st.recall), 3)
              and (st.precision_vs_slice is None or DAX_HEADER[m][0] == 0
                   or round(dax_caught / DAX_HEADER[m][0], 3) == round(float(st.precision_vs_slice), 3)))
        n_ok += ok
        if not ok:
            mismatches.append(("pair", m, t, (dax_caught, dax_n), (s.caught, s.n_pairs, st.recall, st.precision_vs_slice)))
print(f"{n_ok}/{n_cells} cells match (caught, n_pairs, recall vs stored, precision_vs_slice vs stored)")

print("\n=== 2b. Final layered system per type (recall_check_rs shape) ===")
fin = DAX_PAIRS["Final layered system"]
for t, dc, dn in zip(TYPES, fin, DAX_PAIRS_DENOM):
    s = pairs[("Final layered system", t)]
    print(f"{t:22} DAX {dc:>4}/{dn:<4} = {dc/dn:.3f}   SQL {s.caught:>4}/{s.n_pairs:<4} = {s.caught/s.n_pairs:.3f}   "
          f"{'MATCH' if (dc, dn) == (s.caught, s.n_pairs) else 'MISMATCH'}")

print("\n=== 3. Final layered system per detectability tier ===")
for tr, (dn, dc) in DAX_TIER_FINAL.items():
    s = tier[tr]
    ok = (dn, dc) == (s.n_pairs, s.caught)
    if not ok:
        mismatches.append(("tier", tr, (dn, dc), (s.n_pairs, s.caught)))
    print(f"{tr:8} DAX {dc}/{dn} = {dc/dn:.3f}   SQL {s.caught}/{s.n_pairs} = {s.caught/s.n_pairs:.3f}   {'MATCH' if ok else 'MISMATCH'}")

print("\n=== 4. Operating point (eval_threshold_sweep is_chosen) ===")
op_ok = all(close(a, b) for a, b in zip(DAX_OP, (op.threshold, op.recall, op.precision, op.analyst_hours_per_month)))
if not op_ok:
    mismatches.append(("op", DAX_OP, tuple(op)))
print(f"DAX {DAX_OP}\nSQL {tuple(float(x) for x in op)}\n{'MATCH' if op_ok else 'MISMATCH'}")
# Cross-check: the sweep's chosen row must equal the L4 method's own flags.
l4 = hdr["L4 XGBoost @ 0.57"]
xok = close(l4.tp / (l4.tp + l4.fn), op.recall) and close(l4.tp / (l4.tp + l4.fp), op.precision)
print(f"sweep@0.57 vs L4 flag rows: {'MATCH' if xok else 'MISMATCH'}")
if not xok:
    mismatches.append(("op-vs-L4",))

print(f"\nTOTAL MISMATCHES: {len(mismatches)}")
for x in mismatches:
    print("  ", x)
sys.exit(1 if mismatches else 0)
