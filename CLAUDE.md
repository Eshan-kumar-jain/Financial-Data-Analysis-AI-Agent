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
  formatting - all of that is manual in Desktop, the MCP has no reach into
  the report canvas.
- Every DAX measure that has a SQL equivalent must be validated against
  that SQL before it's used on a report page - run both, compare the
  numbers, and record the comparison (which SQL query, which DAX measure,
  match or mismatch) rather than trusting the DAX in isolation.

## Hard rules
- `ground_truth` is labels only. It must never be joined into a feature
  table or used as a model input. That is leakage.
- `reversal_flag` is retrospective. Use it for labelling only, never as
  a feature, for the same reason.
- Seeded errors must vary in detectability. If everything is easy to
  catch, the recall number is meaningless.

## Structure
/sql        staging, star schema, feature table
/notebooks  01_eda, 02_methods, 03_reconciliation, 04_supervised, 05_evaluation
/data       generation scripts, ground truth
/powerbi    .pbix and DAX notes
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
correlation with is_manual_entry/is_after_hours/is_last_two_days is
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

Next: Phase 5, evaluation (notebooks/05_evaluation.ipynb) - score 4a and
4b against ground_truth as two separate scoreboards per the Phase plan.
4a's notebooks (01-03) were also re-executed against the regenerated DB as
part of this fix and are current.

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
  formatting are manual in Desktop, since the MCP has no reach into the
  report canvas.

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
