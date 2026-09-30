"""Validate streamlit_app/metrics.py against the SQL that validates the DAX.

The SQL reference queries are imported from scripts/validate_dax.py - the
same text, not a copy - so the Power BI measures and the Streamlit metrics
are both checked against one definition of each number. metrics.py reads the
Parquet snapshot; the SQL reads Postgres. A match therefore also proves the
snapshot is current (re-run scripts/export_parquet.py if it isn't).

Counts must match exactly; ratios to 3 dp. Exit code non-zero on any mismatch.

Usage (repo root, .env populated):
  .venv/Scripts/python.exe scripts/validate_streamlit.py
"""
import math
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "streamlit_app"))

import metrics  # noqa: E402
from validate_dax import (SQL_HEADER, SQL_LAYER_HEADER, SQL_LAYER_PAIRS, SQL_OP,  # noqa: E402
                          SQL_PAIRS, SQL_TIER, engine)

# Workload splits exist only in the Streamlit/PBIR pages, so they get their own
# reference SQL: final layered system TP / FP per fiscal period and per poster.
SQL_WORKLOAD_PERIOD = """
SELECT e.fiscal_period,
       COUNT(*) FILTER (WHERE f.outcome = 'TP') AS tp,
       COUNT(*) FILTER (WHERE f.outcome = 'FP') AS fp
FROM eval_entry_flag f
JOIN eval_entry      e ON e.header_id = f.header_id
WHERE f.method_name = 'Final layered system'
GROUP BY e.fiscal_period
"""

# Posters are pseudonymised the same way scripts/export_parquet.py does it,
# so the keys line up without a real name ever leaving the database.
SQL_WORKLOAD_POSTER = """
WITH poster AS (
    SELECT employee_key,
           'Poster ' || LPAD(ROW_NUMBER() OVER (ORDER BY employee_key)::text, 2, '0') AS poster
    FROM dim_employee
)
SELECT p.poster,
       COUNT(*) FILTER (WHERE f.outcome = 'TP') AS tp,
       COUNT(*) FILTER (WHERE f.outcome = 'FP') AS fp
FROM eval_entry_flag f
JOIN eval_entry      e ON e.header_id = f.header_id
JOIN poster          p ON p.employee_key = e.employee_key
WHERE f.method_name = 'Final layered system'
GROUP BY p.poster
"""

SQL_SCOREBOARD = "SELECT scoreboard, method_name FROM eval_method WHERE scoreboard IS NOT NULL"


class Checker:
    """Tallies checks per group and records every mismatch."""

    def __init__(self):
        self.groups, self.mismatches = {}, []

    def eq(self, group, label, got, want, dp=None):
        g = self.groups.setdefault(group, [0, 0])
        g[0] += 1
        if dp is None:
            ok = int(got) == int(want)
        else:
            ok = (math.isnan(got) and want is None) or round(float(got), dp) == round(float(want), dp)
        if ok:
            g[1] += 1
        else:
            self.mismatches.append((group, label, got, want))


def main():
    data = metrics.load()
    ck = Checker()

    with engine.connect() as c:
        hdr = {r.method_name: r for r in c.execute(text(SQL_HEADER))}
        pairs = {(r.method_name, r.error_type): r for r in c.execute(text(SQL_PAIRS))}
        tier = {r.detectability: r for r in c.execute(text(SQL_TIER))}
        op = c.execute(text(SQL_OP)).one()
        lay = {r.method_name: r for r in c.execute(text(SQL_LAYER_HEADER))}
        lay_pairs = {(r.method_name, r.error_type): r for r in c.execute(text(SQL_LAYER_PAIRS))}
        per_period = {r.fiscal_period: r for r in c.execute(text(SQL_WORKLOAD_PERIOD))}
        per_poster = {r.poster: r for r in c.execute(text(SQL_WORKLOAD_POSTER))}
        boards = {}
        for r in c.execute(text(SQL_SCOREBOARD)):
            boards.setdefault(r.scoreboard, set()).add(r.method_name)

    # 1. header_metrics - every method, counts exact, ratios 3 dp
    all_methods = metrics.methods(data)
    ck.eq("sanity", "16 methods in snapshot", len(all_methods), len(hdr))
    for m in all_methods:
        s, got = hdr[m], metrics.header_metrics(data, m)
        for k in ("flags", "tp", "fp", "fn"):
            ck.eq("header counts", f"{m}.{k}", got[k], getattr(s, k))
        p = s.tp / (s.tp + s.fp) if s.tp + s.fp else None
        r = s.tp / (s.tp + s.fn)
        ck.eq("header ratios", f"{m}.precision", got["precision"], p, 3)
        ck.eq("header ratios", f"{m}.recall", got["recall"], r, 3)
        ck.eq("header ratios", f"{m}.f1", got["f1"], 2 * p * r / (p + r) if p and (p + r) else None, 3)
        ck.eq("header ratios", f"{m}.flag_rate", got["flag_rate"], s.flags / s.n, 3)
        ck.eq("header ratios", f"{m}.analyst_hours", got["analyst_hours_per_month"], s.fp * 5 / 60 / 6, 3)

    # 2. scoreboard() - membership of A and B, and never merged
    for b in ("A", "B"):
        ck.eq("scoreboards", f"{b} members", set(metrics.scoreboard(data, b)["method"]) == boards[b], True)

    # 3. pair_recall by error_type - 16 methods x 8 types
    for m in all_methods:
        pr = metrics.pair_recall(data, m).set_index("error_type")
        flags = hdr[m].flags
        for et, row in pr.iterrows():
            s = pairs[(m, et)]
            ck.eq("pair recall", f"{m}/{et}.caught", row["caught"], s.caught)
            ck.eq("pair recall", f"{m}/{et}.n_pairs", row["n_pairs"], s.n_pairs)
            ck.eq("pair recall", f"{m}/{et}.recall", row["recall"], s.caught / s.n_pairs, 3)
            ck.eq("pair recall", f"{m}/{et}.precision_vs_slice", row["precision_vs_slice"],
                  s.caught / flags if flags else 0, 3)

    # 4. pair_recall by detectability - final system
    for tr, row in metrics.pair_recall(data, metrics.FINAL, by="detectability").set_index("detectability").iterrows():
        ck.eq("tier recall", f"{tr}.caught", row["caught"], tier[tr].caught)
        ck.eq("tier recall", f"{tr}.recall", row["recall"], tier[tr].caught / tier[tr].n_pairs, 3)

    # 5. operating_point
    got = metrics.operating_point(data)
    for k in ("threshold", "recall", "precision", "analyst_hours_per_month"):
        ck.eq("operating point", k, got[k], getattr(op, k), 3)

    # 6. layer_attribution + layer_pairs
    la = metrics.layer_attribution(data).set_index("layer")
    for m, row in la.iterrows():
        s = lay[m]
        for k, sk in (("first_catch_flags", "fc_flags"), ("first_catch_tp", "fc_tp"),
                      ("unique_flags", "u_flags"), ("unique_tp", "u_tp")):
            ck.eq("layer attribution", f"{m}.{k}", row[k], getattr(s, sk))
    ck.eq("layer attribution", "first-catch sums to final flags",
          la["first_catch_flags"].sum(), hdr[metrics.FINAL].flags)
    for row in metrics.layer_pairs(data).itertuples(index=False):
        s = lay_pairs.get((row.layer, row.error_type))
        ck.eq("layer pairs", f"{row.layer}/{row.error_type}.fc", row.first_catch_pairs, s.fc_pairs if s else 0)
        ck.eq("layer pairs", f"{row.layer}/{row.error_type}.u", row.unique_pairs, s.u_pairs if s else 0)

    # 7. workload - per period and per pseudonymised poster
    wp = metrics.workload(data, by="fiscal_period").set_index("fiscal_period")
    ck.eq("workload", "period count", len(wp), len(per_period))
    for k, row in wp.iterrows():
        ck.eq("workload", f"{k}.tp", row["tp"], per_period[k].tp)
        ck.eq("workload", f"{k}.fp", row["fp"], per_period[k].fp)
    wpo = metrics.workload(data, by="poster").set_index("poster")
    for k, row in wpo.iterrows():
        ck.eq("workload", f"{k}.tp", row["tp"], per_poster[k].tp)
        ck.eq("workload", f"{k}.fp", row["fp"], per_poster[k].fp)

    # 8. the single-method guard itself
    try:
        metrics.header_metrics(data, all_methods[:2])
        ck.eq("guards", "list of methods rejected", False, True)
    except TypeError:
        ck.eq("guards", "list of methods rejected", True, True)

    print(f"{'check group':22} {'checked':>8} {'matched':>8}  result")
    for g, (n, ok) in ck.groups.items():
        print(f"{g:22} {n:>8} {ok:>8}  {'MATCH' if n == ok else 'MISMATCH'}")
    total = sum(n for n, _ in ck.groups.values())
    print(f"{'total':22} {total:>8} {total - len(ck.mismatches):>8}")
    print(f"\nTOTAL MISMATCHES: {len(ck.mismatches)}")
    for x in ck.mismatches[:40]:
        print("  ", x)
    sys.exit(1 if ck.mismatches else 0)


if __name__ == "__main__":
    main()
