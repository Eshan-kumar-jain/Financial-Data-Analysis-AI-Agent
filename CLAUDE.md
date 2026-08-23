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

## Hard rules
- `ground_truth` is labels only. It must never be joined into a feature
  table or used as a model input. That is leakage.
- `reversal_flag` is retrospective. Use it for labelling only, never as
  a feature, for the same reason.
- Seeded errors must vary in detectability. If everything is easy to
  catch, the recall number is meaningless.

## Structure
/sql        staging, star schema, feature table
/notebooks  01_eda, 02_methods, 03_evaluation
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
Next: Phase 4a, statistical + unsupervised detection (notebooks/02_methods).

## Phase plan
- Phase 2 - feature table in SQL (window functions off journal_header/
  journal_line/bank_transactions). ground_truth never joined in - leakage.
- Phase 3 - EDA (notebooks/01_eda): distributions, seasonality, sanity vs.
  the Phase 1/1b checks.
- Phase 4a - statistical + unsupervised detection (notebooks/02_methods):
  Benford's Law, segmented z-score/IQR, Isolation Forest (sklearn), fuzzy
  ledger-to-bank matching (rapidfuzz).
- Phase 4b - supervised detection (notebooks/02_methods): logistic
  regression, random forest, XGBoost. Time-based train/test split (train
  on earlier fiscal periods, test on later - no shuffling across time,
  that would leak future patterns into the past). SHAP for feature
  importance/explainability.
- Phase 5 - evaluation (notebooks/03_evaluation): score 4a and 4b against
  ground_truth as two separate scoreboards, not blended into one ranking -
  precision/recall by detectability tier for each, per the Hard rules
  requirement that a single flat recall number is meaningless.

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
