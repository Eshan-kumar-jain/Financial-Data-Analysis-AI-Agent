# Project: Financial anomaly detection

## Goal
Analyse synthetic journal entry data to detect posting errors and anomalous
transactions. Evaluate detection methods against seeded ground truth.
The deliverables are analysis and dashboards, not a shippable product.

## Framing
This is a data analyst / data science portfolio project.
Weight the work accordingly: roughly 60% SQL, 25% Power BI, 15% Python.
If a task can be done in SQL, do it in SQL.

## Tool rules
- SQL for all cleaning, joins, aggregation, and feature building.
  Use window functions rather than pandas groupby.
- Python ONLY for: statistical tests (scipy), unsupervised/statistical
  detection - Benford's Law, segmented z-score/IQR, Isolation Forest
  (sklearn) - fuzzy matching (rapidfuzz), supervised models - logistic
  regression / random forest / XGBoost (sklearn, xgboost) with a
  time-based split, SHAP for explainability, and evaluation metrics.
- No pandas pipelines that duplicate what SQL already does.
- Postgres is the single source of truth. No intermediate CSVs.

## Style
- Explain SQL logic in comments as you write it. I need to be able to
  defend every window function in an interview.
- Keep notebooks narrative: markdown between cells, not walls of code.
- No premature abstraction. Readable beats clever.
- Ask before adding a dependency.

## Notebooks
Use jupysql %%sql cells for queries. SQL should be visible in the
notebook, not buried in pd.read_sql strings. Pull to pandas only
when a chart or a statistical test needs it.

## Secrets
Connection details come from env vars (PGHOST, PGDATABASE, PGUSER,
PGPASSWORD, PGPORT). Never hardcode credentials. .env is gitignored;
.env.example is committed.

## Power BI MCP
Server: powerbi-modeling-mcp. Must run **locally**, on the same machine as
Power BI Desktop - a remote/hosted connector cannot work, since its
localhost resolves to its own server, not this machine.

Registration: Claude Code **local** scope (`-s local`, stored in
~/.claude.json under this project path), NOT in .mcp.json - so it is not
committed, and a fresh clone (or a different machine/user) must register it
again. The binary ships inside the VS Code extension
analysis-services.powerbi-modeling-mcp (v1.0.0 at time of writing):
`C:\Users\ESHAN JAIN\.vscode\extensions\analysis-services.powerbi-modeling-mcp-1.0.0-win32-x64\server\powerbi-modeling-mcp.exe`.
Args `--start --accept-eula` are the extension's own defaults (package.json
`args` setting, launched as stdio by src/mcpServerManager.ts); --accept-eula
accepts Microsoft's EULA non-interactively. Re-register with:
`claude mcp add powerbi-modeling-mcp -s local -- "<exe path>" --start --accept-eula`
The path embeds the extension version - an extension update changes it and
breaks the registration; re-run the command with the new path. Check with
`claude mcp get powerbi-modeling-mcp`.
Gotcha: ~/.claude.json keys projects by path with case-sensitive drive
letter, and the VS Code extension opens this folder as `e:/...` while the
CLI from a shell registers under `E:/...`. The entry must exist under BOTH
keys (the CLI can't write the lowercase one - copy it by hand), or the
server shows "Connected" in `claude mcp get` yet never loads in VS Code.

Prerequisite: Power BI Desktop must already be open with the .pbix loaded
before connecting. The server only detects Desktop instances running on
localhost - it can't launch or load one for you.

Connection loop, every session (the port is not stable across restarts,
never cache it):
1. `ListLocalInstances` -> returns the model name and port. Re-run this
   every session (and after any Desktop restart) - the port changes each
   time Desktop restarts.
2. Connect with `Data Source=localhost:<PORT>;Application Name=MCP-PBIModeling`,
   using the port from step 1.
3. `dax_query_operations` -> `Execute` to run DAX, e.g.
   `EVALUATE ROW("Total Revenue", [Total Revenue])`.

If `ListLocalInstances` returns empty: either Desktop isn't open, the
.pbix isn't loaded yet, or the MCP got registered as a remote connector
instead of local - check the registration before assuming the model itself
is broken.

Scope - what this MCP is and isn't for in this project:
- **Use it for**: authoring and testing DAX measures, inspecting the model
  (relationships, columns, measure definitions), and cross-checking a
  measure against the equivalent SQL from `notebooks/05_evaluation.ipynb`.
- **Do not use it for**: report page layout, visuals, slicers, or
  formatting - the MCP has no reach into the report canvas. Those are
  authored as PBIR JSON by scripts/build_report.py (see Current phase,
  "Report pages"), or by hand in Desktop.
- Every DAX measure that has a SQL equivalent must be validated against
  that SQL before it's used on a report page - run both, compare the
  numbers, and record the comparison (which SQL query, which DAX measure,
  match or mismatch) rather than trusting the DAX in isolation.
- `eval_entry_flag.score` is **not comparable across methods** - each
  family puts a different quantity in that one column (Benford a MAD in
  percentage points, segmented z a `|z|`, IQR a distance in IQRs past the
  fence, Isolation Forest a negated `decision_function`, the models a
  probability in 0-1). Any DAX measure that reads `score` must be
  filtered to a single `method_name`. Never SUM, AVERAGE, MAX or rank
  `score` across methods, and never put two methods' scores on one axis -
  the result is arithmetic over incompatible units and it will look
  plausible. Counting flags (`is_flagged`) is the cross-method
  comparison; `score` is a within-method one.

## Hard rules
- `ground_truth` is labels only. It must never be joined into a feature
  table or used as a model input. That is leakage.
- `reversal_flag` is retrospective. Use it for labelling only, never as
  a feature, for the same reason.
- Seeded errors must vary in detectability. If everything is easy to
  catch, the recall number is meaningless.

## Structure
/sql        staging, star schema, feature table, eval output tables
/notebooks  01_eda, 02_methods, 03_reconciliation, 04_supervised, 05_evaluation
/data       generation scripts, ground truth
/powerbi    .pbip project (PBIR report + TMDL model), legacy .pbix, DAX notes
/scripts    validate_dax.py (DAX vs SQL), build_report.py (PBIR pages)
/docs       findings write-up

## Data shape targets
- ~250k journal lines across 24 months
- Amounts right-skewed (log-normal), not uniform
- Clear month-end volume spike
- Posting hours cluster 08:00-18:00 with a small after-hours tail
- Seeded errors at roughly 3% of entries
- Bank feed mirrors ledger cash movements, but references are deliberately
  messy (abbreviations, truncation, inconsistent casing, dropped invoice
  numbers, transposed words) - exact-match join to the ledger must fail on
  most rows, or fuzzy matching (rapidfuzz) has nothing to do
- transaction_date lag (vs. posting_datetime) is long-tailed: baseline lag
  for every entry is small (0-2 days, normal processing delay), seeded
  'backdated' errors redraw from a heavier tail out past 30 days
- Baseline (non-seeded) entries also carry a tiny legitimate long-lag tail
  (~0.2% past 9 days) so raw lag>9 isn't a perfect classifier for seeded
  'backdated' on its own
- Seeded 'backdated' targets are NOT drawn uniformly at random: selection is
  weighted toward manual source, after-hours posting, and period-end
  posting (RISK_WEIGHT in data/generate_journal_entries.py::
  seed_backdated_errors = manual 4.3, after_hours 2.7, period_end 2.0,
  calibrated by odds-ratio against each factor's baseline prevalence in the
  eligible pool, then checked empirically). This holds even within the
  'hard' detectability tier (lag 0-2, same range as baseline lag) - deliberate,
  since an earlier analysis (sql/backdated_hard_tier_signal.sql) found the
  hard tier was statistically indistinguishable from clean entries on every
  axis when selection was uniform-random, making lag the only signal.
  Calibrated result (hard tier vs. clean baseline): manual source 28.5% vs
  6.2%, after-hours 23.0% vs 7.2%, period-end posting 54.1% vs 26.7%. ~18%
  of the hard tier (UNDETECTABLE_FRAC = 0.175 target) is deliberately left
  with none of these three drivers present - genuinely undetectable by
  design, not a calibration gap, so hard-tier recall has an honest ceiling
  below 100%.

## Current phase
Phases 2-5 done, evaluation outputs published to Postgres (Phase 5b),
Phase 6 (Power BI) done - semantic model, 24 measures validated against
SQL, five report pages generated as PBIR. See the end of this section.

Phase 2 done - feature table built in sql/02_features.sql (journal_entry_
features, one row per journal_header, 113,981 rows, no ground_truth join).
Amount/timing/behavioural/structural features per the Phase 2 plan below,
plus a chronological train/test split column (first 18 fiscal periods vs.
last 6). Frequency features (user_account_frequency, user_entry_count_month,
account_pair_frequency, is_first_time_pair, account_amount_zscore,
employee_entry_seq) are all running/expanding windows ordered by
posting_datetime so no test-period information leaks into train-period
feature values; account_amount_zscore is additionally NULL below 10 prior
entries for the same account (account_entry_seq <= 10) rather than
coalesced, since a mean/stddev from a handful of points is noise.
employee_entry_seq (running count of prior entries by that employee,
1-indexed, expanding window same shape as account_entry_seq) is
deliberately left ungated - no "new employee" threshold applied in SQL,
since EDA found posting volume per employee follows a Zipf/power-law
distribution (data/generate_journal_entries.py::pick_employee, top employee
alone is ~20% of all headers - see notebooks/01_eda.ipynb finding #8), so
where to draw a "new/inexperienced employee" cutoff is a modelling decision
for Phase 4b, not a fixed rule to bake into the feature table now.
pct_of_account_monthly_total stays a full-period sum since fiscal periods
never straddle the train/test split.

Phase 3 done - EDA in notebooks/01_eda.ipynb, executed against the live DB
(jupysql %%sql cells, blind to ground_truth). 12 numbered findings covering
scale/shape, structural cleanliness, the 338 unbalanced headers, posting-hour/
day-of-week/month-end patterns, posting lag, employee/account volume
concentration, and amount splits by account_type/role/manual-vs-system.

Phase 4a done - notebooks/02_methods.ipynb (Benford, segmented z-score/IQR,
Isolation Forest, method overlap) and notebooks/03_reconciliation.ipynb
(two-stage bank reconciliation: SQL blocking on amount + 0-3 day date
window, then rapidfuzz on reference strings), both executed against the
live DB, both blind to ground_truth. Reconciliation split into its own
notebook rather than folded into 02_methods so the blind unsupervised work
stays separate from the labelled work that comes later. Key results: JE
number recovered from the bank reference resolves 53.9% of bank rows
outright; partial_ratio at threshold 70 (chosen over token_sort_ratio and
WRatio - see the notebook's Section 5/7 findings, the candidate string has
no counterparty text so whole-string scorers are structurally capped)
recovers a further 12.6%, leaving 32.9% unmatched and 0.6% true blocking
orphans. Section 9 diagnosed that 32.9% by re-blocking with 100x amount
tolerance and a 5x wider date window: zero rows had a better candidate
under the wider block, so the loss is 100% a scoring/threshold problem, not
a blocking problem. Section 10 replaced the pure-string threshold with a
composite score (weighted amount exactness + date proximity + string
similarity, ranked instead of string alone) and a no-text baseline (accept
the unique blocked candidate outright when amount is exact and date is in
window): the baseline alone clears 99.2% of Stage 2 (since amount_diff is
exactly 0.00 for 100% of candidates in this dataset - bank amounts mirror
the ledger with no rounding noise - so blocking-plus-uniqueness is nearly
the whole answer here), a moderate composite blend
(w_amount=w_date=0.3, w_string=0.4 @ threshold 0.50) clears 78.4%, and
composite ranking (vs. string-only ranking) picks a different top
candidate in 48.7% of the 195 multi-candidate blocks. Total blind coverage
including Stage 1: string-only 66.5%, composite 89.6%, baseline 99.0% - the
baseline's number is dataset-specific (perfect amount mirroring) rather
than a generally trustworthy rule, so the composite is the recommended
production default. Whether any of this is actually right is left for
Phase 5 to score against ground_truth, on purpose.

Phase 4b done - notebooks/04_supervised.ipynb, executed against the live DB.
This is the one notebook that queries ground_truth - joined into a labelled
frame by header_id only, at training/eval time, never written back into
journal_entry_features (Hard rules). Labels rolled up per header via
array_agg(DISTINCT error_type/detectability) since 18 of ~5,000 flagged
headers carry two ground_truth rows (unmatched_bank's ledger-side pool is
drawn independently of the other error types' shared eligible pool).
Time-based split reused as-is (train=first 18 periods, test=last 6).
Imbalance handled via class_weight="balanced" (logistic regression, random
forest) and scale_pos_weight computed from y_train only (XGBoost) - not
SMOTE/resampling, since error_type is heterogeneous enough that
interpolating between e.g. a structuring positive and a backdated positive
would synthesize a pattern that was never actually seeded. SHAP
(shap.TreeExplainer on XGBoost, exact) run both as a global beeswarm over a
2,000-row test sample and as individual waterfall plots contrasting an
easy-tier high-confidence catch against the hard-tier test positive the
model is least confident about.

Section 11 audited every seeding routine in data/generate_journal_entries.py
for fields it writes that journal_entry_features reads, and found one real
leak: structuring appended literal " (split n/m)" to entry_description for
every split header, which is why description_length ranked #3 in the SHAP
importance and why structuring hit a suspicious 1.000 recall across all
three models despite being labelled 'hard' detectability - the model was
reading the seeding mechanism's fingerprint, not the intended
near-$10k-threshold amount pattern. No other error type's seeding routine
writes a field outside its intended signal: round_number/unbalanced only
touch line amounts (the former is the designed is_round_* signal, the
latter a small random nudge with no fixed marker); duplicate clones a
header verbatim (a genuine behavioural duplicate, not an artifact);
off_hours_posting/unusual_account_pair only touch posting_datetime+
employee_key / account_key respectively, both exactly the designed
timing/structural signal; backdated only writes transaction_date (its
correlation with is_manual_entry/is_after_hours/is_period_end is
deliberate SELECTION bias toward headers that already have those
properties, not a field mutation - documented, intentional, not a leak);
unmatched_bank never touches journal_header/journal_line at all (it only
adds/drops bank_transactions rows, which journal_entry_features doesn't
read - can't leak into a table it never joins).

**Fixed**: the " (split n/m)" suffix was removed from
data/generate_journal_entries.py's structuring block (the split
relationship now lives in ground_truth.notes only); data regenerated
end-to-end with the same RNG_SEED=42 (sql/01_schema.sql reset ->
generator re-run -> sql/00_sanity_checks.sql re-verified identical row
counts/balance/error mix/lag/amount distributions to the pre-fix run, only
entry_description text differs); journal_entry_features rebuilt; notebooks
01-04 re-executed against the regenerated DB, 0 errors across all four.
Section 11.2 is kept as the record of the investigation (before-fix numbers
stated explicitly, then the fix, then the post-fix numbers confirming it
worked) rather than scrubbed once resolved.

Post-fix test-period results (threshold 0.5): logistic regression precision
0.172/recall 0.696/AP 0.408; random forest precision 0.739/recall 0.555/
AP 0.604; XGBoost precision 0.448/recall 0.626/AP 0.638. Recall by
detectability tier: easy ~0.68-0.77, medium ~0.40-0.60, hard ~0.55-0.70.
structuring's own recall dropped from the pre-fix 1.000/1.000/1.000 to a
realistic 0.864/0.682/0.868 (LR/RF/XGB) - still fairly high since the
intended amount/structural signal is real, but no longer a trivial
giveaway. Key finding (unchanged by the fix, structuring wasn't the cause
of this one): RF/XGB recall collapses to near zero (0.02-0.11) on
unbalanced, duplicate, and unmatched_bank specifically, because
journal_entry_features has no direct feature for any of the three (no
imbalance-magnitude, no duplicate-proximity count, no reconciliation
status - the last one is deliberately 03_reconciliation.ipynb's problem,
not this feature table's); restricting recall to the five addressable
types (round_number/backdated/off_hours_posting/unusual_account_pair/
structuring) shows 0.71-0.84 across all three models, confirming the gap
is feature coverage, not a model weakness.

SHAP top-15 post-fix (grouped by the same amount/timing/behavioural/
structural taxonomy sql/02_features.sql uses): user_account_frequency
(behavioural) leads, with account_pair_frequency (structural) and
total_amount (amount) close behind at #2/#3 - a three-way contest at the
top rather than the behavioural sweep the pre-fix ranking (with
description_length falsely inflated to #3) appeared to show. Phase 3 EDA's
prediction that behavioural signal would outrank structural: partially
confirmed either way, but the post-fix ranking is the honest version of
that comparison.

Phase 5 done - notebooks/05_evaluation.ipynb, executed against the live DB,
80 cells, 0 errors. Every 4a and 4b method re-run on the same test set
(last 6 fiscal periods, 28,695 headers, 1,293 positives / 1,297 (header,
error_type) pairs) and scored against ground_truth as two separate
scoreboards.

Scoreboard A (unsupervised, blind - the realistic estimate): best F1 is
segmented IQR at precision 0.248 / recall 0.295 / F1 0.269; best AP is
Isolation Forest at 0.215; segmented z-score is the precision corner
(0.464 at recall 0.111); Isolation Forest is the AP leader at 0.214.
Benford at entry level is indistinguishable from
random (precision 0.058 vs a 0.045 base rate, AP 0.049) - not a failure of
the law but a unit mismatch, since an account-level digit test pushed down
to that account's entries flags them all equally; MAD ranking still
identifies the deviating accounts correctly. The blind methods are
amount-shaped detectors: IQR catches 59.9% of round_number and 77.7% of
structuring and <7% of everything else. Medium-tier recall collapses to
0.04-0.08 for every blind method. The account_entry_seq > 10 restriction
costs no coverage in the test period (every test account is past warm-up);
only reconciliation has restricted coverage (0.42, cash headers only).

Reconciliation scored two ways, settling 03_reconciliation.ipynb's open
question: "unmatched after text scoring" gets recall 1.000 at precision
0.060, while the purely structural "no blocking candidate within a cent
and 0-3 days" gets precision 1.000 / recall 0.988 on the ledger side and
1.000 / 0.987 on the bank side. That confirms Section 9's blind diagnosis
against labels - the loss was scoring, not blocking - and the structural
variant is what the layered system uses.

Scoreboard B (supervised, optimistic): LR 0.172/0.695/AP 0.405, RF
0.748/0.554/AP 0.606, XGB 0.440/0.621/AP 0.638. Labels are worth ~3x
average precision over the best blind method on the same rows. Addressable
recall 0.705-0.833 vs unaddressable 0.041-0.226.

Per-type winners change per row, and once change scoreboard: models win
round_number (1.000), off_hours_posting (LR 0.961), unusual_account_pair
(XGB 0.893), structuring (LR 0.872, XGB level at 0.847); blind
reconciliation wins
unmatched_bank 0.988 vs <=0.171 for every model, with zero false
positives. LR's per-type "wins" are bought with an 18.2% flag rate and
4,312 false positives, so recall is always read next to the flag-rate
column.

Layered system (the notebook's main argument - method choice follows error
structure, not model sophistication): L1 SQL balance check (GROUP BY +
HAVING) scores 1.000/1.000 on unbalanced; L2 SQL duplicate self-join
(same employee/account-set/amount within 5 days, prior.header_id <
e.header_id so only the later entry is flagged) scores recall 1.000 /
precision 0.723, its 53 false positives being genuine repeat postings; L3
reconciliation-by-blocking scores 0.976/0.988. Stacked with XGBoost at the
same threshold, recall goes 0.621 -> 0.835 AND precision 0.440 -> 0.502
for 326 extra flags - precision rises because the added flags are nearly
all true positives. Per-type delta is +0.957 duplicate, +0.927
unmatched_bank, +0.908 unbalanced, ~0.000 on the behavioural types.

Combined scoring - negative result, reported as such: soft-vote ensemble
AP 0.6373 < XGBoost alone 0.6375, rank-average 0.6123; the unsupervised
rule union reaches recall 0.504 but F1 falls to 0.208 (vs 0.269 for the
best single blind method), and the cross-scoreboard union gives recall
0.749 at F1 0.274 (vs XGB's 0.515 and RF's 0.636). The soft-vote's F1 edge
at the arbitrary 0.50 cut (0.524 vs XGB 0.515) is called out as a
calibration artifact, not a win - both trail RF there, and AP still
favours XGB. Ensembling models that read the same
23 features adds opinions, not information; the layered stack works
because its layers read different inputs entirely.

Threshold selection: cost model is 5 analyst-minutes per false positive
against an expected 480 x 20% = 96 minutes per miss, i.e. ~19:1, giving
threshold 0.57 (recall 0.610, precision 0.573, ~8 analyst-hours/month).
The undiscounted 96:1 reading is kept in the notebook because it is
instructive - it picks threshold 0.10 and flags 91% of the ledger, which
is what any linear cost model does without an escalation probability and a
capacity constraint. F1-optimal (0.79) is argued against explicitly: it
implicitly prices a missed error at ~31 analyst-minutes. A 5%-review-budget
capacity check supports thresholds down to 0.56, and the chosen 0.57 fits
inside it (4.8% flag rate; the full layered stack at 6.0% sits marginally
over). Final configuration (SQL layers + XGBoost @ 0.57): recall 0.830,
precision 0.627, F1 0.715, ~9 analyst-hours/month, >=0.79 recall on every
error type except backdated.

Threshold stability is itself a finding: the is_period_end alignment (see
below) moved the cost-optimal threshold 0.46 -> 0.57 while moving AP by
0.001, so the cost surface is shallow across ~0.45-0.60 and the operating
point is reported as a band to be reviewed against realised workload, not
as a two-decimal constant.

backdated is the one real remaining gap: 0.260 against a measured design
ceiling of 0.838 (best hard-tier recall by any method: LR 0.346). Closing
the gap needs features journal_entry_features still doesn't have:
post_lag_days against the poster's own lag history rather than a global
threshold, and an interaction over the three risk drivers rather than
three independent flags.

**Feature alignment fix (done after the first Phase 5 run).**
sql/02_features.sql's period-end feature was is_last_two_days (the last 2
CALENDAR days of the fiscal period) while the generator weights backdating
selection on dim_date.is_month_end (the last 3 BUSINESS days). Measuring
the undetectable backdated slice through the mismatched proxy gave ~42%
against the generator's actual UNDETECTABLE_FRAC of 17.5%, which would
have understated the ceiling to ~0.58. The feature is now
`is_period_end = dim_date.is_month_end`, keyed off journal_header.date_key
exactly as seed_backdated_errors keys it; days_from_period_end is
unchanged (continuous distance, not the binary driver). Feature table
rebuilt, notebooks 02/04/05 re-executed, 0 errors. Supervised numbers moved
by less than a point - XGB 0.448/0.626/AP 0.6385 -> 0.440/0.621/AP 0.6375,
RF 0.739/0.555 -> 0.748/0.554, LR unchanged at 0.172/0.695 - i.e. the fix
mattered for the honesty of the ceiling measurement, not for detection
performance. All numbers above are post-alignment.

Limitations section covers synthetic-data provenance, labels recording the
seeding mechanism rather than fraud, the backdated ceiling, the
reconciliation ceiling (a candidate_ref construction choice, not a
rapidfuzz limit), the description_length leak and the fact that it had
distorted the Phase 3 EDA hypothesis verdict before the fix, employee
concentration (39 posters, top 15 = 88.6%, busiest single = 19.8%), the
single seed/split with no variance estimate, and the fact that the blind
methods are fitted over the whole ledger including test rows.

Phase 5b done - evaluation outputs written back to Postgres so Power BI
reads the database rather than a CSV export. DDL in sql/03_eval_outputs.sql
(drop/recreate, re-runnable), filled by Section 10 of
notebooks/05_evaluation.ipynb from the same in-memory series every Section
2-7 number is computed from - not scraped out of the rounded display
frames, so a dashboard figure cannot drift from the notebook's arithmetic.
Six tables:

- eval_method (16 rows) - method dimension: family (unsupervised/
  supervised/layer/combined/final), scoreboard A/B (NULL for layers and
  combinations - they are comparisons, not a third ranking), threshold,
  note.
- eval_entry (28,695) - one test header: fiscal_period, date_key,
  employee_key, total_amount, is_error, n_labels, final_flag,
  final_outcome (TP 1,073 / FP 637 / FN 220 / TN 26,765).
- eval_entry_label (1,297 pairs, 1,293 headers) - the pair grain, taken
  straight off the notebook's `pairs` frame. This is what makes per-type
  recall pair-level in DAX.
- eval_entry_flag (459,120 = 16 methods x 28,695) - long, not 16 flag
  columns, so one DAX measure serves every method behind a slicer.
  is_flagged, is_covered, score, outcome. score is NULL outside a
  method's coverage and is NOT comparable across methods (Benford MAD,
  |z|, IQR distance, negated decision_function, probability all share the
  column) - rank within a method, never average across them.
- eval_method_score (208) - the scoreboard, long by slice_type
  (overall 16 / error_type 128 / detectability 48 / addressability 16).
  precision, f1 and average_precision are NULL on every non-'overall'
  row: a false positive has no error_type, so there is no honest per-type
  precision denominator. precision_vs_slice (true positives of that type
  over the method's TOTAL flags) is the defined alternative and is only
  flattering to a targeted layer, which is the point of it.
  addressability rows are emitted only for the unsupervised and
  supervised families, since the two lists differ (Scoreboard A addresses
  unmatched_bank via reconciliation, B does not) and the split is
  meaningless for a stack built to cover what a model cannot address.
- eval_threshold_sweep (99) - the cost curve for XGBoost across the
  0.01-0.99 grid, with is_chosen on 0.57 and cost_fn_fp_ratio stored on
  every row so a reader who disagrees with the 19:1 assumption can see
  which one produced the pick.

These are REPORTING tables and they contain labels (is_error,
eval_entry_label, every precision/recall column derive from
ground_truth). Showing recall without labels is impossible, so this is
intended - but they must never be joined into journal_entry_features or
used as a model input. Routing ground_truth through a differently-named
table does not stop it being ground_truth. The warning is repeated at the
top of sql/03_eval_outputs.sql and in Section 10's intro.

Section 10 ends with a parity check that re-reads the written tables and
asserts against the notebook: all 16 methods' precision/recall match to
3dp, per-type pair-level recall matches Section 7 for all 8 error types,
and the operating point round-trips (threshold 0.57, recall 0.610,
precision 0.573, 8.2 analyst-hours/month for XGBoost alone; the final
layered system is 1,710 flags at precision 0.627 / recall 0.830 / F1
0.715). Notebook re-executed end-to-end, 93 cells, 0 errors.

**Per-slice double-counting bug (found in Phase 6, fixed).** Section 10's
slice_rows built eval_method_score's error_type/detectability/
addressability rows with `caught.groupby(...).groups` + `caught.loc[idx]`.
`pairs` is indexed by header_id, which repeats for a doubly-labelled
header, so .loc returned both of that header's rows and every flagged
2-label header was counted twice in the slice numerator (denominator was
right). Symptom: Reconciliation / unmatched_bank stored at 85 caught of 82
pairs, recall 1.037; 39 per-type cells inflated in total, all on
unmatched_bank or the partner label of the 4 test-period 2-label headers
(2 structuring, 1 off_hours_posting, 1 unusual_account_pair). Fix: iterate
the groups directly (`for value, hits in caught.groupby(keys.values)`).
Scope: ONLY the non-'overall' rows of eval_method_score (recall,
true_positives, precision_vs_slice). The 'overall' rows never touch
`pairs`; Section 7 and every other per-type table in the notebook go
through recall_by (column-wise groupby().mean(), no label lookup) and were
never wrong - no reported figure changed. Why the parity check missed it:
it read back the 'overall' rows and recall_check_rs, never the per-slice
rows, so the one code path with the bug had no test. A round-trip check
only covers the rows it actually reads. Section 10 now has a
`%%sql slice_check_rs` cell rebuilding every error_type/detectability row
from eval_entry_flag x eval_entry_label, and the parity cell asserts exact
counts on those, reconciles the addressability buckets against them, and
asserts no recall > 1 (192 per-slice rows pass). A markdown cell after the
parity check records the bug and the gap. Notebook re-executed, 95 cells,
0 errors.

**Rerunning notebook 05 recreates the eval_* tables** (drop/recreate via
Section 10), which drops any per-table grants. Power BI connects as the
`powerbi` role; after the rerun above, refresh failed on all eight tables
until SELECT was re-granted. Fixed with `GRANT SELECT ON ALL TABLES IN
SCHEMA public TO powerbi` plus `ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO powerbi` - the default privileges are what keep
the role working across future recreations. Caveat: without FOR ROLE,
ALTER DEFAULT PRIVILEGES only covers tables created by the role that ran
it - it must be the same role as the notebook's PGUSER. If refresh
fails with a permission error after a rerun, check those first.

Phase 6 in progress - Power BI semantic model in powerbi/
anomaly_detection.pbix, built through the MCP. Model: 8 tables (dim_date,
dim_employee, eval_entry, eval_entry_flag, eval_entry_label, eval_method,
eval_method_score, eval_threshold_sweep; "public " prefix stripped;
dim_account dropped - no eval_* table carries account_key), 7
relationships, dim_date marked as date table. The auto-detected
eval_method[threshold] -> eval_threshold_sweep[threshold] relationship was
deleted: the sweep is XGBoost-only, so it mapped z-score/IQR/LR/RF cutoffs
onto XGBoost's curve, on a float key. Auto date/time is switched off in
Desktop by hand.

18 measures, every one blank unless exactly one method is in context
(hidden [Methods In Context] guard - never aggregate across methods):
Header-level (eval_entry_flag: Flags, True Positives, False Positives,
False Negatives, Precision, Recall, F1, Flag Rate, Analyst Hours per
Month), Pair-level (eval_entry_label: Label Pairs, Pairs Caught, Pair
Recall, Precision vs Slice), Operating point (eval_threshold_sweep: Chosen
Threshold/Recall/Precision/Analyst Hours per Month). Pairs Caught uses
TREATAS to push the method's flagged header_ids onto
eval_entry_label[header_id] - label filters (error_type/detectability)
live on eval_entry_label and do not reach eval_entry_flag through the
single-direction relationships, and TREATAS combines the two without a
bidirectional relationship. Counts pairs, not headers.

scripts/validate_dax.py is the artefact behind "every DAX measure validated
against SQL". It compares three sources: DAX results (captured through the
MCP and pasted into the script's DAX_* constants - Python cannot query the
Desktop model; the four DAX queries are in its docstring), SQL recomputed
from the eval_* base rows (the reference; recall_check_rs generalised to
all 16 methods), and the stored eval_method_score rows. Checks: header-
level flags/TP/FP/FN for 16 methods, precision/recall/analyst-hours vs
stored, 16 x 8 pair-level caught/n_pairs plus stored recall and
precision_vs_slice, Final layered system per tier, and the operating point
(including the sweep's chosen row vs the L4 method's own flag rows). Exit
code non-zero on any mismatch. Run from the repo root with the venv:
`.venv/Scripts/python.exe scripts/validate_dax.py`. It is what caught the
per-slice bug (39 mismatches before the fix, 0 after). Last run
2026-09-30 against the refreshed model: 0 mismatches. After any model or
data change, re-run the DAX queries, update the constants, re-run.

Layer attribution (the last Section 9 figure) is built: 6 measures in the
"Layer attribution" folder - First-Catch Flags/True Positives/Pairs Caught
(credit to the earliest layer in running order L1 -> L4 by
eval_method[sort_order]; partitions the stack, sums exactly to the Final
layered system's 1,710 flags / 1,073 TP / per-type caught) and Unique
Flags/True Positives/Pairs Caught (caught by this layer and no other -
what removing it would cost; does not sum). Blank unless one 'layer'
method is selected. validate_dax.py section 5 checks them against SQL
computed a different way (per-header MIN(sort_order)/COUNT over flagging
layers, not EXCEPT over sets): 4/4 layers, 32/32 layer x type cells, the
partition sum - 0 mismatches. Result: only 17 of 1,710 flagged headers are
caught by 2+ layers; L1/L2/L3 own unbalanced/duplicate/unmatched_bank and
L4 first-catches none of those three.

powerbi/dax_notes.md records the model decisions, the rules every measure
follows, and a measure -> validating-SQL -> result table for all 24
measures, plus the re-validation procedure and report-page usage notes.

**Report pages - generated as PBIR, not drawn by hand.** The MCP cannot
reach the report canvas, so the report was converted to a Power BI Project:
powerbi/anomaly_detection.pbip (source of truth) with
anomaly_detection.Report/ (PBIR JSON: one folder per page, one visual.json
per visual) and anomaly_detection.SemanticModel/ (TMDL - the model built
through the MCP, now diffable text). scripts/build_report.py generates all
five pages (33 visuals) - Overview, Layered detection, Method comparison,
Operating point, Review workload - from one readable Python file; every
visual uses the validated measures, and each page filters to a single
method or family so no measure ever sees more than one method. Output is
checked against Microsoft's published PBIR JSON schemas (github
microsoft/json-schemas; 54 files, 0 failing). Rules for editing:
- Run build_report.py with Desktop CLOSED - Desktop holds the files and
  overwrites them on save. Then open the .pbip (not the .pbix).
- The theme ("Tidal", chosen in Desktop) and report.json are NOT managed
  by the script; it leaves them alone. Meaningful series colours (errors
  found blue #2a78d6, false alarms orange #eb6834 - validated categorical
  palette) are set per visual so they survive any theme.
- Desktop-authored mobile layouts (visuals/<name>/mobile.json) are kept
  across a regenerate by visual name - so append new visuals at the END of
  a page's list, or existing visuals get renumbered and lose their mobile
  layout.
- Desktop rewrites visual.json on save (schema 2.4.0 -> 2.12.0, adds
  active:true, drops isDefaultSort) - cosmetic, the script's output is
  equivalent.
- Table totals are OFF on purpose: at the total row every method is in
  context and the measures blank (summing across methods is invalid), so an
  empty total would look like a broken measure and a computed one would be
  wrong.
- eval_method[method_name] has sortByColumn: sort_order (TMDL edit), so
  methods and layers always list in pipeline order L1 -> L4.
- Review workload page names individual employees; a note on the page says
  a production deployment would use role-based access or pseudonymised
  poster IDs.
The old anomaly_detection.pbix is kept as a legacy snapshot and does NOT
contain the report pages. .gitignore excludes **/.pbi/localSettings.json
(a DPAPI-encrypted, machine-bound security binding) and **/.pbi/cache.abf.

Phase 6 is complete apart from polish: layout tweaks go through
build_report.py, measure changes through the MCP + validate_dax.py.

## Phase plan
- Phase 2 - feature table in SQL (window functions off journal_header/
  journal_line/bank_transactions). ground_truth never joined in - leakage.
- Phase 3 - EDA (notebooks/01_eda): distributions, seasonality, sanity vs.
  the Phase 1/1b checks.
- Phase 4a - statistical + unsupervised detection: Benford's Law, segmented
  z-score/IQR, Isolation Forest (sklearn) in notebooks/02_methods; fuzzy
  ledger-to-bank matching (rapidfuzz) in its own notebooks/03_reconciliation
  - two-stage (SQL blocking on amount/date, then rapidfuzz on reference
  text), threshold-swept, kept separate from 02_methods so blind
  unsupervised work doesn't blend with the labelled work in Phase 4b/5.
- Phase 4b - supervised detection (notebooks/04_supervised): logistic
  regression, random forest, XGBoost. Time-based train/test split (train
  on earlier fiscal periods, test on later - no shuffling across time,
  that would leak future patterns into the past). SHAP for feature
  importance/explainability.
- Phase 5 - evaluation (notebooks/05_evaluation): score 4a (methods +
  reconciliation) and 4b against ground_truth as two separate scoreboards,
  not blended into one ranking - precision/recall by detectability tier for
  each, per the Hard rules requirement that a single flat recall number is
  meaningless.
- Phase 6 - Power BI (/powerbi). Split by tool, not by task: the semantic
  model and DAX measures are built and tested through the Power BI MCP
  (see that section above) - relationships, measure definitions, and
  validating each measure against its SQL equivalent from Phase 5's
  notebook before it's trusted; report page layout, visuals, slicers, and
  formatting are outside the MCP's reach, so they are generated as PBIR
  (Power BI Project) JSON by scripts/build_report.py instead.

## Schema (as built, sql/01_schema.sql)

dim_account
  account_key      SERIAL PK
  account_id       VARCHAR(10)  UNIQUE NOT NULL
  account_name     VARCHAR(100) NOT NULL
  account_type     VARCHAR(20)  NOT NULL  CHECK IN (Asset,Liability,Equity,Revenue,Expense)
  normal_balance   VARCHAR(6)   NOT NULL  CHECK IN (Debit,Credit)
  is_cash_account  BOOLEAN      NOT NULL

dim_employee
  employee_key         SERIAL PK
  employee_id          VARCHAR(10)  UNIQUE NOT NULL
  employee_name        VARCHAR(100) NOT NULL
  department            VARCHAR(50)  NOT NULL
  role                   VARCHAR(30)  NOT NULL
  seniority_level        VARCHAR(10)  NOT NULL  CHECK IN (junior,mid,senior)
  typical_start_hour     SMALLINT NOT NULL  CHECK 0-23
  typical_end_hour       SMALLINT NOT NULL  CHECK 0-23

dim_date
  date_key       INT PK            -- YYYYMMDD
  calendar_date  DATE UNIQUE NOT NULL
  year/month/day SMALLINT NOT NULL
  day_of_week    SMALLINT NOT NULL -- 0=Mon
  day_name       VARCHAR(10) NOT NULL
  is_weekend     BOOLEAN NOT NULL
  is_month_end   BOOLEAN NOT NULL  -- last 3 business days of month
  fiscal_period  VARCHAR(7) NOT NULL -- 'YYYY-MM'

journal_header
  header_id           BIGSERIAL PK
  header_id_text       VARCHAR(20) UNIQUE NOT NULL  -- 'JE-000001'
  posting_datetime     TIMESTAMP NOT NULL
  transaction_date     DATE NOT NULL  -- when the event happened vs. posting_datetime
                                       -- (when it was recorded). Not FK'd to dim_date -
                                       -- a large backdating lag can fall outside the window.
  date_key             INT  NOT NULL  FK -> dim_date.date_key
  employee_key         INT  NOT NULL  FK -> dim_employee.employee_key
  source_system        VARCHAR(20) NOT NULL
  entry_description    VARCHAR(200) NULL
  is_reversal          BOOLEAN NOT NULL DEFAULT FALSE
  reversed_header_id   BIGINT NULL  FK -> journal_header.header_id  (self)
  reversal_flag        BOOLEAN NOT NULL DEFAULT FALSE  -- retrospective, label-only, never a feature

journal_line
  line_id           BIGSERIAL PK
  header_id         BIGINT NOT NULL  FK -> journal_header.header_id
  line_num          SMALLINT NOT NULL  -- UNIQUE(header_id, line_num)
  account_key       INT NOT NULL  FK -> dim_account.account_key
  debit_amount      NUMERIC(14,2) NOT NULL DEFAULT 0  CHECK >= 0
  credit_amount     NUMERIC(14,2) NOT NULL DEFAULT 0  CHECK >= 0
  line_description  VARCHAR(200) NULL

bank_transactions
  bank_txn_id     BIGSERIAL PK
  value_date      DATE NOT NULL
  amount          NUMERIC(14,2) NOT NULL  -- signed: + in / - out
  reference       VARCHAR(140) NULL       -- deliberately messy, see Data shape targets
  counterparty    VARCHAR(120) NULL
  -- NOT FK'd to journal_header/journal_line on purpose: a real bank feed
  -- doesn't know your JE numbers. Matching it to the ledger is a
  -- fuzzy-matching problem (rapidfuzz), not a join. value_date lags the
  -- mirrored ledger posting by 0-3 days (clearing delay).

ground_truth
  gt_id           BIGSERIAL PK
  header_id       BIGINT NULL  FK -> journal_header.header_id
  line_id         BIGINT NULL  FK -> journal_line.line_id
  bank_txn_id     BIGINT NULL  FK -> bank_transactions.bank_txn_id
  error_type      VARCHAR(30) NOT NULL  CHECK IN (duplicate, round_number, unbalanced,
                    off_hours_posting, unusual_account_pair, structuring,
                    unmatched_bank, backdated)
  detectability   VARCHAR(10) NOT NULL  CHECK IN (easy, medium, hard)
  notes           VARCHAR(300) NULL
  CHECK (header_id IS NOT NULL OR line_id IS NOT NULL OR bank_txn_id IS NOT NULL)
  -- never join into a feature table (leakage, see Hard rules)
  -- unmatched_bank labels either side of an orphan pair: header_id/line_id
  -- set + bank_txn_id null = ledger cash line with no bank counterpart;
  -- bank_txn_id set + header_id/line_id null = phantom bank row with no
  -- ledger counterpart.

Balance check per entry: SUM(debit_amount) - SUM(credit_amount) OVER
(PARTITION BY header_id) should be 0 except the 338 seeded 'unbalanced' rows.

Sanity checks: sql/00_sanity_checks.sql (row counts, balance check, error_type
x detectability, amount/hour distributions, transaction_date lag buckets,
unmatched_bank direction split). Re-run after any regeneration.
