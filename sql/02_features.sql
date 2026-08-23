-- ============================================================================
-- 02_features.sql
-- Phase 2: feature table for anomaly detection, one row per journal_header.
--
-- Design choices worth defending:
--   - Multi-line entries need a single "account" for account-level features
--     (z-score, monthly share, pairing). We pick the PRIMARY account as the
--     line carrying the largest single debit/credit movement in the entry,
--     and separately the primary debit-side and primary credit-side accounts
--     to build an account_pair for structural features. This collapses an
--     N-line entry to a defensible single account/pair without discarding
--     which side actually moved the most money.
--   - Frequency/history features (user_account_frequency, user_entry_count_
--     month, account_pair_frequency, is_first_time_pair) are RUNNING counts
--     (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW, ordered by
--     posting_datetime) rather than full-sample counts. "How familiar is
--     this user with this account" only accumulates forward in time - a
--     full-sample count would let a January entry see March's history,
--     which would leak future information into a supposedly-past feature
--     once Phase 4b does its time-based train/test split.
--   - account_amount_zscore is ALSO a running stat (expanding window,
--     ordered by posting_datetime), same reasoning as the frequency
--     features above - a full-partition mean/stddev would let a
--     train-period row's z-score be computed from test-period amounts.
--     It's NULL below 10 prior entries (account_entry_seq <= 10): a
--     mean/stddev from a handful of points is noise, not signal.
--   - pct_of_account_monthly_total stays a full-period (not running) sum:
--     it's partitioned by fiscal_period, and every period sits entirely on
--     one side of the train/test split (18 vs. 6 whole periods), so there
--     is no cross-split leakage. A period total is only fully known at
--     period close anyway - which is also when a controller would
--     realistically run this kind of concentration check.
--   - No ground_truth join anywhere. Every feature below is derived only
--     from journal_header / journal_line / dim_date / dim_employee.
-- ============================================================================

DROP TABLE IF EXISTS journal_entry_features;

CREATE TABLE journal_entry_features AS
WITH

-- 1. line_agg: collapse journal_line to header grain. total_amount is the
--    average of the debit-side and credit-side totals rather than just
--    SUM(debit_amount): for a balanced entry debit total = credit total so
--    the average equals either; for the handful of seeded 'unbalanced'
--    entries it still gives one honest number instead of picking a side
--    arbitrarily.
line_agg AS (
    SELECT
        header_id,
        COUNT(*)                                          AS entry_line_count,
        ROUND(SUM(debit_amount + credit_amount) / 2, 2)   AS total_amount
    FROM journal_line
    GROUP BY header_id
),

-- 2. primary_account: the account carrying the single largest debit-or-
--    credit movement in the entry (largest-line rule). DISTINCT ON +
--    ORDER BY is Postgres' idiomatic "top-1-per-group" - cheaper than a
--    window function + filter because it only needs one sorted scan per
--    header, not a full window frame materialization. Ties broken by
--    line_num ASC, which is a debit-side tiebreak in practice: the
--    generator always writes an entry's debit lines before its credit
--    lines (data/generate_journal_entries.py), so line_num 1..k are debit
--    and k+1.. are credit - on a tied largest-line amount, the lower
--    line_num picks the debit side.
primary_account AS (
    SELECT DISTINCT ON (header_id)
        header_id,
        account_key AS primary_account_key
    FROM journal_line
    ORDER BY header_id, (debit_amount + credit_amount) DESC, line_num ASC
),

-- 3. primary_debit / primary_credit: the dominant debit-side and dominant
--    credit-side account, independently. Together they define the entry's
--    account_pair for structural features (this is exactly what
--    ground_truth's 'unusual_account_pair' error type is trying to catch -
--    the feature has to be built without ever touching that label).
--    LEFT JOIN'd later since a malformed/one-sided entry could in principle
--    have no debit or no credit line.
primary_debit AS (
    SELECT DISTINCT ON (header_id)
        header_id,
        account_key AS debit_account_key
    FROM journal_line
    WHERE debit_amount > 0
    ORDER BY header_id, debit_amount DESC, line_num ASC
),
primary_credit AS (
    SELECT DISTINCT ON (header_id)
        header_id,
        account_key AS credit_account_key
    FROM journal_line
    WHERE credit_amount > 0
    ORDER BY header_id, credit_amount DESC, line_num ASC
),

-- 4. base: one row per header, joined to its dimensions and the line-level
--    aggregates above. Every feature past this point is a window function
--    over base - no further joins needed.
base AS (
    SELECT
        h.header_id,
        h.header_id_text,
        h.posting_datetime,
        h.transaction_date,
        h.employee_key,
        h.source_system,
        h.entry_description,
        d.calendar_date,
        d.is_weekend,
        d.fiscal_period,
        la.entry_line_count,
        la.total_amount,
        pa.primary_account_key,
        pd.debit_account_key,
        pc.credit_account_key,
        e.typical_start_hour,
        e.typical_end_hour
    FROM journal_header h
    JOIN dim_date d              ON d.date_key = h.date_key
    JOIN dim_employee e          ON e.employee_key = h.employee_key
    JOIN line_agg la             ON la.header_id = h.header_id
    JOIN primary_account pa      ON pa.header_id = h.header_id
    LEFT JOIN primary_debit pd   ON pd.header_id = h.header_id
    LEFT JOIN primary_credit pc  ON pc.header_id = h.header_id
)

SELECT
    header_id,
    header_id_text,

    -- ================= AMOUNT =================
    total_amount,

    LN(total_amount + 1) AS log_amount,
        -- log1p. CLAUDE.md's data shape target is log-normal, right-skewed
        -- amounts - LN compresses the tail so a $2M entry doesn't dominate
        -- a linear model's loss the way it would on the raw scale. +1
        -- keeps LN defined for a (rare, degenerate) zero-amount entry.

    CASE WHEN total_amount > 0
         THEN FLOOR(total_amount / POWER(10, FLOOR(LOG(10, total_amount))))::SMALLINT
         ELSE NULL
    END AS leading_digit,
        -- Benford's Law input (Phase 4a). Dividing by 10^floor(log10(x))
        -- rescales any positive number into [1,10) without string
        -- parsing; FLOOR of that gives the first significant digit as an
        -- integer 1-9. NULL for a zero-amount entry (no leading digit).

    (MOD(total_amount, 100)   = 0) AS is_round_100,
    (MOD(total_amount, 1000)  = 0) AS is_round_1000,
    (MOD(total_amount, 10000) = 0) AS is_round_10000,
        -- three thresholds, not one: a suspiciously-round $500 entry and a
        -- suspiciously-round $50,000 entry are both round-number risk, but
        -- at different scales - one flag would either fire constantly on
        -- everyday small amounts or miss round-but-large ones.

    COUNT(*) OVER acct_w AS account_entry_seq,
        -- running count of entries posted to this account, as of and
        -- including this one (1-indexed). Same running-window shape as
        -- the behavioural frequency features - exposed so the z-score's
        -- sample-size gate below is auditable rather than a hidden magic
        -- number.

    CASE WHEN COUNT(*) OVER acct_w > 10 THEN
        ROUND(
            (total_amount - AVG(total_amount) OVER acct_w)
            / NULLIF(STDDEV(total_amount) OVER acct_w, 0)
        , 4)
    ELSE NULL
    END AS account_amount_zscore,
        -- std devs from the running mean/stddev of amounts posted to this
        -- SAME account, using only entries up to and including this one
        -- (acct_w is ORDER BY posting_datetime, header_id, ROWS UNBOUNDED
        -- PRECEDING - an expanding window, not a whole-partition one).
        -- This matches how the running frequency features are built and
        -- means a train-period row's z-score can never be computed from
        -- test-period amounts, since test always comes later in time.
        -- Gated on account_entry_seq > 10 (at least 10 PRIOR entries,
        -- since acct_w's count includes the current row): below that,
        -- mean/stddev are too noisy to mean anything, and we return NULL
        -- rather than coalescing to 0, which would silently tell a model
        -- "perfectly average" for an account with no real history yet.
        -- NULLIF still guards the case where the trailing window happens
        -- to be constant (STDDEV = 0).

    ROUND(
        total_amount / NULLIF(SUM(total_amount) OVER acct_month_w, 0)
    , 6) AS pct_of_account_monthly_total,
        -- this entry's share of everything posted to its account within
        -- its own fiscal period.

    -- ================= TIMING =================
    (MAX(calendar_date) OVER period_w - calendar_date) AS days_from_period_end,
        -- 0 = posted on the last calendar day seen in its fiscal period.
        -- Derived from the data (MAX date actually present) instead of a
        -- hardcoded month length, so it's correct across 28/30/31-day
        -- months with no CASE statement.

    ((MAX(calendar_date) OVER period_w - calendar_date) <= 1) AS is_last_two_days,
        -- period-end posting is one of the three factors the generator
        -- weights backdating toward (RISK_WEIGHT.is_period_end in
        -- data/generate_journal_entries.py) - this is that same signal,
        -- built independently in the feature table.

    (EXTRACT(HOUR FROM posting_datetime) < typical_start_hour
     OR EXTRACT(HOUR FROM posting_datetime) >= typical_end_hour) AS is_after_hours,
        -- after-hours relative to THIS employee's own typical window
        -- (dim_employee.typical_start_hour/end_hour), not one global
        -- cutoff - matches the generator's own definition, so a
        -- controller who normally closes the books at 21:00 isn't
        -- flagged for posting at 21:00 while a 9-to-5 clerk posting at
        -- 21:00 is.

    is_weekend,

    (posting_datetime::date - transaction_date) AS post_lag_days,
        -- posting lag in days. Baseline clusters 0-2 (normal processing
        -- delay); seeded 'backdated' errors redraw from a heavier tail
        -- past 30 days. Not a leak - both dates are known at posting
        -- time, unlike reversal_flag which depends on a future entry.

    -- ================= BEHAVIOURAL =================
    COUNT(*) OVER emp_w AS employee_entry_seq,
        -- running count of entries posted by this employee, as of and
        -- including this one (1-indexed) - same running-window shape as
        -- account_entry_seq above, ordered by posting_datetime so it's an
        -- expanding window, not a whole-partition one (no test-period
        -- leakage into train-period values). Left ungated on purpose: no
        -- threshold applied here, unlike account_amount_zscore's >10 gate.
        -- Exposed raw so a "new employee" cutoff can be picked at modelling
        -- time (Phase 4b) rather than baked into the feature table now.

    COUNT(*) OVER user_acct_w AS user_account_frequency,
        -- running count: how many times this employee has posted to this
        -- account, as of and including this entry.

    COUNT(*) OVER user_month_w AS user_entry_count_month,
        -- running count of this employee's entries within the current
        -- fiscal period - a volume-spike signal (someone who normally
        -- posts 5 entries/month suddenly posting 40 is informative on
        -- its own).

    entry_line_count,

    (entry_description IS NOT NULL AND LENGTH(TRIM(entry_description)) > 0) AS has_description,

    COALESCE(LENGTH(entry_description), 0) AS description_length,

    -- ================= STRUCTURAL =================
    debit_account_key,
    credit_account_key,

    COUNT(*) OVER pair_w AS account_pair_frequency,
        -- running count of how many times this exact (debit_account,
        -- credit_account) pair has been used, as of this entry.

    (COUNT(*) OVER pair_w = 1) AS is_first_time_pair,
        -- same running count, reused via the named window (Postgres
        -- computes pair_w's COUNT(*) once and both references read it -
        -- no duplicated window pass): TRUE exactly once, on the pair's
        -- first-ever occurrence.

    (source_system = 'GL_MANUAL') AS is_manual_entry,
        -- manual GL entries skip the sub-ledger controls that AP/AR/
        -- payroll entries go through, and it's the strongest of the
        -- three factors the generator weights backdating toward
        -- (RISK_WEIGHT.is_manual = 4.3, highest of the three).

    -- ================= SPLIT =================
    fiscal_period,
    CASE
        WHEN DENSE_RANK() OVER period_rank_w <= 18 THEN 'train'
        ELSE 'test'
    END AS split
        -- chronological, not random. DENSE_RANK over distinct fiscal
        -- periods gives each period an index 1..24 in calendar order
        -- ('YYYY-MM' sorts correctly as text); first 18 periods -> train,
        -- last 6 -> test. Reads the period count off the data itself
        -- rather than a hardcoded date range, and keeps every entry in a
        -- given period on the same side of the split - required for
        -- Phase 4b's time-based split to mean anything (train on earlier
        -- periods, test on later, no shuffling across time).

FROM base
WINDOW
    acct_w        AS (PARTITION BY primary_account_key
                       ORDER BY posting_datetime, header_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    acct_month_w  AS (PARTITION BY primary_account_key, fiscal_period),
    period_w      AS (PARTITION BY fiscal_period),
    period_rank_w AS (ORDER BY fiscal_period),
    emp_w         AS (PARTITION BY employee_key
                       ORDER BY posting_datetime, header_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    user_acct_w   AS (PARTITION BY employee_key, primary_account_key
                       ORDER BY posting_datetime, header_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    user_month_w  AS (PARTITION BY employee_key, fiscal_period
                       ORDER BY posting_datetime, header_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    pair_w        AS (PARTITION BY debit_account_key, credit_account_key
                       ORDER BY posting_datetime, header_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW);

-- one row per header, and split is the column notebooks will filter on most.
CREATE UNIQUE INDEX ix_journal_entry_features_header_id ON journal_entry_features(header_id);
CREATE INDEX ix_journal_entry_features_split ON journal_entry_features(split);
