# DAX notes: semantic model and measure validation

The semantic model in `anomaly_detection.pbip` (TMDL under
`anomaly_detection.SemanticModel/`) was built and tested through the
Power BI MCP (`powerbi-modeling-mcp`). The report pages are generated as PBIR
by `scripts/build_report.py` (section 7). The older `anomaly_detection.pbix` is
a legacy snapshot without the report pages. Every measure that has a SQL equivalent
has been checked against that SQL. This file records **which SQL validates which
measure**, what the result was, and the design decisions a reviewer is likely to
ask about.

- **Validation artefact:** [`scripts/validate_dax.py`](../scripts/validate_dax.py).
  It exits non-zero on any mismatch.
- **Last run:** 2026-09-30, against the refreshed model. **0 mismatches.**
- **SQL source:** the `eval_*` tables written by Section 10 of
  `notebooks/05_evaluation.ipynb`. DDL is in `sql/03_eval_outputs.sql`.

> The `eval_*` tables are **reporting tables that contain labels** (`is_error`,
> `eval_entry_label`, every precision and recall figure). They exist so the
> dashboard can show how the detectors performed. They must never be joined into
> `journal_entry_features` or used as a model input.

---

## 1. Model

| Table | Grain | Rows | Role |
|---|---|---:|---|
| `eval_method` | one per detection method | 16 | method dimension: family, scoreboard, threshold, sort order |
| `eval_entry` | one per test-period header | 28,695 | evaluation population, carries `is_error` |
| `eval_entry_flag` | one per (header, method) | 459,120 | long fact: every method's verdict on every header |
| `eval_entry_label` | one per (header, error_type) pair | 1,297 | pair grain behind per-type recall |
| `eval_method_score` | one per (method, slice) | 208 | stored scoreboard, used as a third source in validation |
| `eval_threshold_sweep` | one per threshold, 0.01–0.99 | 99 | XGBoost cost curve, `is_chosen` marks 0.57 |
| `dim_date` | one per day | 731 | marked as the date table on `calendar_date` |
| `dim_employee` | one per employee | 40 | poster attributes |

Relationships are all many-to-one and single-direction:

```
eval_entry_flag  ──► eval_entry ──► dim_date
                 │              └─► dim_employee
                 └─► eval_method ◄── eval_method_score
eval_entry_label ──► eval_entry
eval_threshold_sweep   (standalone)
```

Decisions:

- **Deleted `eval_method[threshold] → eval_threshold_sweep[threshold]`.** Power BI
  created it automatically because the column names match. The sweep only covers
  XGBoost, so the link mapped the z-score cutoff (3), the IQR cutoff (1.5) and the
  LR/RF cutoffs (0.5) onto XGBoost's cost curve, and it joined on a decimal
  column. The sweep is read only by the operating-point measures, which don't
  need the link.
- **Dropped `dim_account`.** No `eval_*` table has an `account_key`, so the
  table could not filter anything.
- **Stripped the `public ` prefix** from the table names.
- **Turned off auto date/time**, by hand in Desktop.
- **Did not add a bidirectional relationship** for per-type recall. Per-type
  filters sit on `eval_entry_label`, and method filters reach `eval_entry_flag`.
  Neither reaches the other through the single-direction relationships. The
  pair-level measures bridge them with `TREATAS` (section 3). A bidirectional
  link would make every `eval_entry` filter ambiguous.

---

## 2. Rules every measure follows

1. **Scores are compared within one method only.** Every scoring measure returns
   BLANK unless exactly one method is in context. This is enforced by the hidden
   `[Methods In Context]` guard, `DISTINCTCOUNT(eval_entry_flag[method_name])`.
   Adding flags or TPs across methods double-counts headers.
   `eval_entry_flag[score]` is **never** aggregated: it holds a Benford MAD, a
   `|z|`, an IQR distance, a negated `decision_function` or a probability,
   depending on the method.
2. **Per-type recall is counted in pairs, not headers.** Recall by error type is
   `COUNT` over `eval_entry_label` rows whose header was flagged, divided by
   `COUNT` over `eval_entry_label` rows in the slice. A header with two labels
   counts once for each type. A `DISTINCTCOUNT(header_id)` gives a different
   and wrong number.
3. **Precision does not slice by error type.** A false positive has no error
   type, so header-level `[Precision]` is only meaningful when not filtered by
   `error_type`. The per-type alternative is `[Precision vs Slice]`: true
   positives of that type divided by the method's *total* flags.

---

## 3. Measures and their validation

The "SQL" column names the query constant in `scripts/validate_dax.py`, or the
notebook cell it comes from. "Stored" means the value was also compared against
`eval_method_score` as written by the notebook.

### Header-level (home table `eval_entry_flag`, folder *Header-level*)

| Measure | Definition | Validated against | Result |
|---|---|---|---|
| `Flags` | rows with `is_flagged` for the one method in context | `SQL_HEADER` (`COUNT(*) FILTER (WHERE is_flagged)` per method) | 16/16 methods match |
| `True Positives` | rows with `outcome = "TP"` | `SQL_HEADER` | 16/16 |
| `False Positives` | rows with `outcome = "FP"` | `SQL_HEADER` | 16/16 |
| `False Negatives` | rows with `outcome = "FN"` | `SQL_HEADER` | 16/16 |
| `Precision` | TP / (TP + FP) | stored `eval_method_score` overall rows, to 3 dp | 16/16 |
| `Recall` | TP / (TP + FN) | stored overall rows, to 3 dp | 16/16 |
| `Analyst Hours per Month` | FP × 5 min / 60 / 6 months | stored `analyst_hours_per_month` | 16/16 |
| `F1` | 2PR / (P + R) | built only from the validated Precision and Recall; DAX result checked in session (Final layered system 0.715) | ✓ (not a separate SQL query) |
| `Flag Rate` | Flags / rows in context | built only from the validated Flags; Final layered system 5.96% = 1,710 / 28,695 | ✓ (not a separate SQL query) |
| `Methods In Context` *(hidden)* | guard, see section 2 | no SQL equivalent | n/a |

### Pair-level (home table `eval_entry_label`, folder *Pair-level*)

| Measure | Definition | Validated against | Result |
|---|---|---|---|
| `Label Pairs` | `COUNTROWS(eval_entry_label)` | `SQL_PAIRS` `n_pairs` | 128/128 (16 methods × 8 types) |
| `Pairs Caught` | pairs whose header the method flagged: the method's flagged `header_id`s are pushed onto `eval_entry_label[header_id]` with `TREATAS` | `SQL_PAIRS` (the notebook's `recall_check_rs` query extended to all 16 methods), plus `SQL_TIER` for detectability | 128/128 types, 3/3 tiers |
| `Pair Recall` | Pairs Caught / Label Pairs | `recall_check_rs` (notebook 05, Section 10) and stored `error_type` rows | 128/128; Final layered system per type matches `recall_check_rs` exactly |
| `Precision vs Slice` | Pairs Caught / the method's total Flags | stored `precision_vs_slice` | 128/128 |

### Operating point (home table `eval_threshold_sweep`, folder *Operating point*)

| Measure | Definition | Validated against | Result |
|---|---|---|---|
| `Chosen Threshold` | threshold on the `is_chosen` row | `SQL_OP` | 0.57 ✓ |
| `Chosen Recall` | recall on the `is_chosen` row | `SQL_OP`, and cross-checked against the `L4 XGBoost @ 0.57` rows in `eval_entry_flag` | 0.610 ✓ |
| `Chosen Precision` | precision on the `is_chosen` row | `SQL_OP` + the same L4 cross-check | 0.573 ✓ |
| `Chosen Analyst Hours per Month` | hours on the `is_chosen` row | `SQL_OP` | 8.2 ✓ |

The L4 cross-check matters because it ties together two tables written by
different code in the notebook: the sweep curve and the per-header flags.

### Layer attribution (folder *Layer attribution*)

These measures answer "which layer caught each flagged header" (Section 9 of
notebook 05): do the SQL layers and the model catch *different* things? The
Final layered system is exactly L1 OR L2 OR L3 OR L4. The layers overlap, so
there are two honest ways to credit a catch, and the model has both. They only
return a value when a single `layer` method is selected; they are BLANK for any
other method, including `Final layered system`.

- **First-catch:** credit goes to the earliest layer in running order,
  L1 balance check → L2 duplicate self-join → L3 reconciliation → L4 XGBoost
  @ 0.57 (by `eval_method[sort_order]`). The cheap deterministic SQL checks run
  first and the model last. First-catch counts **partition** the final stack, so
  they add up to the Final layered system's totals. Use them for a stacked bar.
- **Unique:** caught by this layer and by no other. This is what the stack would
  lose if the layer were removed. Unique counts are independent of the running
  order and do **not** add up to the total, because headers caught by two or
  more layers count for no layer.

DAX computes both as `EXCEPT` over sets of `header_id`s. The SQL in
`validate_dax.py` uses a different method on purpose: it reduces each header to
`first_layer = MIN(sort_order)` and `n_layers = COUNT(*)` over the layers that
flagged it. Two independent methods agreeing is stronger evidence than one
method checked against itself.

| Measure | Home table | Validated against | Result |
|---|---|---|---|
| `First-Catch Flags` | `eval_entry_flag` | `SQL_LAYER_HEADER` `fc_flags` | 4/4 layers |
| `First-Catch True Positives` | `eval_entry_flag` | `SQL_LAYER_HEADER` `fc_tp` | 4/4 |
| `Unique Flags` | `eval_entry_flag` | `SQL_LAYER_HEADER` `u_flags` | 4/4 |
| `Unique True Positives` | `eval_entry_flag` | `SQL_LAYER_HEADER` `u_tp` | 4/4 |
| `First-Catch Pairs Caught` | `eval_entry_label` | `SQL_LAYER_PAIRS` `fc_pairs` | 32/32 (4 layers × 8 types) |
| `Unique Pairs Caught` | `eval_entry_label` | `SQL_LAYER_PAIRS` `u_pairs` | 32/32 |
| *(partition check)* | | the four first-catch values must add up to the Final layered system's Flags, TP and per-type Pairs Caught | ✓ 1,710 flags, 1,073 TP, all 8 types |

Values, in the test period:

| Layer | Flags | First-catch flags | First-catch TP | Unique flags | Unique TP |
|---|---:|---:|---:|---:|---:|
| L1 balance check | 76 | 76 | 76 | 71 | 71 |
| L2 duplicate self-join | 191 | 191 | 142 | 183 | 134 |
| L3 reconciliation | 83 | 82 | 82 | 78 | 78 |
| L4 XGBoost @ 0.57 | 1,377 | 1,361 | 773 | 1,361 | 773 |
| **Sum** | | **1,710** | **1,073** | | |

What these numbers show: the layers barely overlap. Only 17 flagged headers are
caught by more than one layer (1,710 minus the 1,693 unique flags). Each layer
owns its own error types:

- L1 catches unbalanced (76 pairs).
- L2 catches duplicate (138).
- L3 catches unmatched_bank (81).
- L4 catches round_number, off_hours_posting, structuring, unusual_account_pair
  and backdated.

L4 catches **none** of the duplicate, unbalanced or unmatched_bank pairs first.
This is the Phase 5 "method choice follows error structure" argument, shown as
counts.

---

## 4. Bug found by this validation

The first run of `validate_dax.py` found **39 mismatches**. DAX and the SQL
recompute agreed on every count, but the stored `eval_method_score` per-type rows
did not. Reconciliation / unmatched_bank was stored at 85 caught against 82
pairs, a recall of 1.037.

The cause was `slice_rows` in notebook 05 Section 10. `pairs` is indexed by
`header_id`, which repeats for a header with two labels, so `caught.loc[idx]`
returned both of that header's rows. Every flagged 2-label header was therefore
counted twice in a slice's numerator.

- **Fixed.** The code now iterates the groups directly.
- **Scope:** only the per-type, per-tier and addressability rows of
  `eval_method_score`. No figure reported in the notebook changed.
- **Why the parity check missed it:** the notebook's parity check read back the
  overall rows and `recall_check_rs`, but never the per-slice rows. Section 10
  now rebuilds and asserts every per-slice row too.

Full write-up: the markdown cell after Section 10's parity check, and CLAUDE.md.

---

## 5. Re-validating after a change

1. Open Desktop with the .pbix loaded. Run `ListLocalInstances` through the MCP.
   The port changes every time Desktop restarts.
2. Refresh the model. If you re-ran notebook 05, the `eval_*` tables were dropped
   and recreated. The `powerbi` role depends on
   `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO powerbi`,
   run as the same role the notebook connects as. If the refresh fails with a
   permission error, check that first.
3. Run the six DAX queries listed in the docstring of `validate_dax.py` and paste
   the results into its `DAX_*` constants.
4. Run `.venv/Scripts/python.exe scripts/validate_dax.py` from the repo root and
   expect `TOTAL MISMATCHES: 0`.

Any new measure that has a SQL equivalent gets a query in the script and a row
in section 3 **before** it goes on a report page.

---

## 6. Using the measures on report pages

- **Put a slicer on `eval_method[method_name]`**, not on
  `eval_entry_flag[method_name]`. The layer attribution measures read
  `eval_method[sort_order]` and `eval_method[method_family]`, which are only
  filtered when the filter comes from `eval_method`.
- **Pick a single method** (single-select slicer, or a filter on the visual).
  Every measure blanks when two or more methods are in context. That is
  deliberate.
- **Show per-type recall by pairs:** put `eval_entry_label[error_type]` or
  `[detectability]` on the axis and use `[Pair Recall]`, never `[Recall]`.
- **Show recall next to cost:** put `[Flag Rate]` or
  `[Analyst Hours per Month]` beside any recall figure, because LR's high recall
  comes with an 18% flag rate.
- **Layer attribution chart:** filter the visual to
  `eval_method[method_family] = "layer"`. Use First-Catch for a stacked total and
  Unique for "what we'd lose without it". A layer × `error_type` matrix of
  `[First-Catch Pairs Caught]` shows the layers catching different types.
- **Keep the two scoreboards separate:** don't put Scoreboard A (unsupervised)
  and Scoreboard B (supervised) methods in one ranked visual. Use
  `eval_method[scoreboard]` to split them.

---

## 7. Report pages

The MCP can't reach the report canvas, so the report is a **Power BI Project**
(`anomaly_detection.pbip`). Each page and visual is a PBIR JSON file, and
[`scripts/build_report.py`](../scripts/build_report.py) generates all of them.
The output is checked against Microsoft's published PBIR JSON schemas.

| Page | Question it answers | Filter scope | Measures used |
|---|---|---|---|
| Overview | How well does the final system work? | page: `Final layered system` | Recall, Precision, F1, Flags, Analyst Hours per Month; Pair Recall by `error_type` and by `detectability` |
| Layered detection | Do the layers catch different things? | page: `method_family = layer` | First-Catch Flags / True Positives; Flags, Unique Flags / True Positives; First-Catch Pairs Caught (layer × error_type heatmap) |
| Method comparison | Blind methods vs labelled models | visual: `scoreboard = A`, `scoreboard = B`; heatmap: families unsupervised + supervised | Precision, Recall, F1, Flag Rate, Analyst Hours per Month; Pair Recall (method × error_type heatmap) |
| Operating point | Why a 0.57 threshold? | none (the sweep table is XGBoost-only) | Chosen Threshold / Recall / Precision / Analyst Hours; sweep columns `precision`, `recall`, `analyst_hours_per_month`, `expected_cost_minutes` |
| Review workload | What would an analyst face? | page: `Final layered system` | Flags, True Positives, False Positives, Flag Rate, by `fiscal_period` and by poster |

Design choices:

- **Every visual sees exactly one method.** Each method table and matrix has
  `eval_method[method_name]` on rows; the other visuals get a page or visual
  filter. This follows rule 1 in section 2.
- **Table totals are off.** At the total row every method is in context, so the
  measures return blank. A computed total would add values across methods,
  which is invalid.
- **No dual axes.** Precision and recall share one 0–1 axis. Workload and
  expected cost each have their own chart.
- **Colours that carry meaning are set on each visual**, so a theme change
  can't repaint them: errors found are blue `#2a78d6`, false alarms orange
  `#eb6834`. The theme itself ("Tidal") is chosen in Desktop, and the script
  leaves it alone.
- **Privacy:** the Review workload page names individual posters. A note on
  the page says a production deployment would use role-based access or
  pseudonymised IDs.

To regenerate the pages:

1. Close Desktop, because it overwrites the files on save.
2. From the repo root, run `.venv/Scripts/python.exe scripts/build_report.py`.
3. Open `anomaly_detection.pbip`.

Mobile layouts made in Desktop are kept across a regenerate, matched by visual
name, so add new visuals at the end of a page's list.
