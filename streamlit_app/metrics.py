"""Every metric the Streamlit app shows - the one place metric logic lives.

Pure pandas over the Parquet snapshot (no Streamlit import), so
scripts/validate_streamlit.py can import it and check each function against
the same SQL scripts/validate_dax.py uses. The definitions mirror the DAX
measures in powerbi/dax_notes.md one for one.

Rules carried over from dax_notes.md:
  1. Single method only. Every function that scores takes ONE method name
     (a str) and raises on anything else - there is no code path that adds
     flags, TPs or precision across methods.
  2. eval_entry_flag.score is never read. It holds a Benford MAD, a |z|, an
     IQR distance, a negated decision_function or a probability depending on
     the method; nothing here aggregates it.
  3. Per-type recall is PAIR-level: counted over eval_entry_label rows, so a
     header with two error types counts once for each.
  4. Per-type precision is not defined (a false positive has no error type);
     precision_vs_slice is the defined alternative.
"""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
TABLES = ["eval_method", "eval_entry", "eval_entry_label", "eval_entry_flag",
          "eval_threshold_sweep", "poster"]

FINAL = "Final layered system"
FP_MINUTES = 5          # analyst-minutes per false positive (Phase 5 cost model)
TEST_MONTHS = 6         # test split = last 6 fiscal periods
BAND = (0.45, 0.60)     # operating band: the cost surface is shallow across it
FULL_LEDGER_HEADERS = 113_981   # journal_entry_features rows (all 24 periods) - context only


def load(data_dir=DATA_DIR):
    """Read the Parquet snapshot. The app wraps this in st.cache_data."""
    return {t: pd.read_parquet(Path(data_dir) / f"{t}.parquet") for t in TABLES}


# --- guards ------------------------------------------------------------------
def _one_method(data, method):
    """Rule 1: exactly one method, by name. Returns that method's flag rows."""
    if not isinstance(method, str):
        raise TypeError(f"metrics take ONE method name, got {type(method).__name__} - "
                        "aggregating across methods is invalid by design")
    known = set(data["eval_method"]["method_name"])
    if method not in known:
        raise ValueError(f"unknown method {method!r}")
    return data["eval_entry_flag"].loc[data["eval_entry_flag"]["method_name"] == method]


def methods(data, family=None, scoreboard=None):
    """Method names in pipeline order (sort_order), optionally filtered."""
    m = data["eval_method"]
    if family is not None:
        m = m[m["method_family"].isin([family] if isinstance(family, str) else family)]
    if scoreboard is not None:
        m = m[m["scoreboard"] == scoreboard]
    return m.sort_values("sort_order")["method_name"].tolist()


# --- header level (DAX folder: Header-level) -----------------------------------
def _counts(flags):
    tp = int((flags["outcome"] == "TP").sum())
    fp = int((flags["outcome"] == "FP").sum())
    fn = int((flags["outcome"] == "FN").sum())
    return {"flags": int(flags["is_flagged"].sum()), "tp": tp, "fp": fp, "fn": fn, "rows": len(flags)}


def _ratios(c):
    precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else float("nan")
    recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else float("nan")
    return {**c,
            "precision": precision, "recall": recall, "f1": f1,
            "flag_rate": c["flags"] / c["rows"] if c["rows"] else float("nan"),
            # FP x 5 min, over the 6-month test period -> hours per month
            "analyst_hours_per_month": c["fp"] * FP_MINUTES / 60 / TEST_MONTHS}


def header_metrics(data, method):
    """Flags, TP, FP, FN, precision, recall, F1, flag rate, analyst hours - one method."""
    return _ratios(_counts(_one_method(data, method)))


def scoreboard(data, board):
    """Scoreboard 'A' (unsupervised) or 'B' (supervised) - never both in one table.
    One row per method, each computed on its own (rule 1)."""
    if board not in ("A", "B"):
        raise ValueError("scoreboard is 'A' or 'B'; the two are never merged into one ranking")
    rows = [{"method": m, **header_metrics(data, m)} for m in methods(data, scoreboard=board)]
    return pd.DataFrame(rows)


# --- pair level (DAX folder: Pair-level) ---------------------------------------
def pair_recall(data, method, by="error_type"):
    """Pair-level recall by error_type or detectability for one method.
    caught = label pairs whose header this method flagged (the TREATAS measure)."""
    flags = _one_method(data, method)
    flagged = set(flags.loc[flags["is_flagged"], "header_id"])
    labels = data["eval_entry_label"].assign(caught=lambda d: d["header_id"].isin(flagged))
    out = (labels.groupby(by)
                 .agg(n_pairs=("caught", "size"), caught=("caught", "sum"))
                 .reset_index())
    out["recall"] = out["caught"] / out["n_pairs"]
    out["precision_vs_slice"] = out["caught"] / max(len(flagged), 1)
    return out


def recall_matrix(data, family=("unsupervised", "supervised"), by="error_type"):
    """Method x error_type pair recall, one method per row (each computed alone)."""
    parts = [pair_recall(data, m, by).assign(method=m) for m in methods(data, family=family)]
    return (pd.concat(parts)
              .pivot(index="method", columns=by, values="recall")
              .reindex(methods(data, family=family)))


# --- layer attribution (DAX folder: Layer attribution) --------------------------
def _layer_header_facts(data):
    """Per header: first flagging layer (lowest sort_order) and how many layers flagged it.
    Same reduction as validate_dax.py's SQL_LAYER_HEADER."""
    layers = data["eval_method"].loc[data["eval_method"]["method_family"] == "layer",
                                     ["method_name", "sort_order"]]
    f = data["eval_entry_flag"].merge(layers, on="method_name")
    flagged = f[f["is_flagged"]]
    facts = flagged.groupby("header_id")["sort_order"].agg(first_layer="min", n_layers="size",
                                                          only_layer="max").reset_index()
    return layers, facts


def layer_attribution(data):
    """Per layer: flags, first-catch flags/TP (sum to the final system), unique flags/TP."""
    layers, facts = _layer_header_facts(data)
    is_error = data["eval_entry"].set_index("header_id")["is_error"]
    facts = facts.assign(is_error=facts["header_id"].map(is_error).astype(bool))
    rows = []
    for name, order in layers.sort_values("sort_order").itertuples(index=False):
        first = facts["first_layer"] == order
        only = (facts["n_layers"] == 1) & (facts["only_layer"] == order)
        rows.append({
            "layer": name,
            "flags": header_metrics(data, name)["flags"],
            "first_catch_flags": int(first.sum()),
            "first_catch_tp": int((first & facts["is_error"]).sum()),
            "unique_flags": int(only.sum()),
            "unique_tp": int((only & facts["is_error"]).sum()),
        })
    return pd.DataFrame(rows)


def layer_pairs(data):
    """Layer x error_type: first-catch and unique label pairs."""
    layers, facts = _layer_header_facts(data)
    lab = data["eval_entry_label"].merge(facts, on="header_id")
    rows = []
    for name, order in layers.sort_values("sort_order").itertuples(index=False):
        first = lab["first_layer"] == order
        only = (lab["n_layers"] == 1) & (lab["only_layer"] == order)
        g = pd.DataFrame({"error_type": lab["error_type"], "fc": first, "u": only}).groupby("error_type").sum()
        for et, r in g.iterrows():
            rows.append({"layer": name, "error_type": et,
                         "first_catch_pairs": int(r["fc"]), "unique_pairs": int(r["u"])})
    out = pd.DataFrame(rows)
    # every (layer, error_type) cell, zero-filled, like the DAX matrix
    grid = pd.MultiIndex.from_product([layers.sort_values("sort_order")["method_name"],
                                       sorted(data["eval_entry_label"]["error_type"].unique())],
                                      names=["layer", "error_type"])
    return out.set_index(["layer", "error_type"]).reindex(grid, fill_value=0).reset_index()


# --- operating point (DAX folder: Operating point) -------------------------------
def sweep(data):
    return data["eval_threshold_sweep"].sort_values("threshold")


def operating_point(data):
    chosen = data["eval_threshold_sweep"].loc[data["eval_threshold_sweep"]["is_chosen"]].iloc[0]
    return {"threshold": float(chosen["threshold"]), "recall": float(chosen["recall"]),
            "precision": float(chosen["precision"]),
            "analyst_hours_per_month": float(chosen["analyst_hours_per_month"]),
            "cost_fn_fp_ratio": float(chosen["cost_fn_fp_ratio"])}


# --- workload ------------------------------------------------------------------
def workload(data, method=FINAL, by="fiscal_period"):
    """TP / FP per fiscal_period or per (pseudonymised) poster, for one method."""
    flags = _one_method(data, method)
    e = data["eval_entry"][["header_id", "fiscal_period", "employee_key"]]
    f = flags.merge(e, on="header_id")
    if by == "poster":
        f = f.merge(data["poster"][["employee_key", "poster"]], on="employee_key")
    out = (f.assign(tp=f["outcome"] == "TP", fp=f["outcome"] == "FP")
             .groupby(by)[["tp", "fp"]].sum().astype(int).reset_index())
    out["flags"] = out["tp"] + out["fp"]
    return out


def test_split_size(data):
    return len(data["eval_entry"])
