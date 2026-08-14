-- ============================================================================
-- 00_sanity_checks.sql
-- Repeatable post-load checks for the synthetic data generator. Re-run after
-- any regeneration (data/generate_journal_entries.py) or schema change.
-- ============================================================================

-- 1. Row counts.
SELECT 'dim_account' AS table_name, COUNT(*) FROM dim_account
UNION ALL SELECT 'dim_employee', COUNT(*) FROM dim_employee
UNION ALL SELECT 'dim_date', COUNT(*) FROM dim_date
UNION ALL SELECT 'journal_header', COUNT(*) FROM journal_header
UNION ALL SELECT 'journal_line', COUNT(*) FROM journal_line
UNION ALL SELECT 'bank_transactions', COUNT(*) FROM bank_transactions
UNION ALL SELECT 'ground_truth', COUNT(*) FROM ground_truth
ORDER BY 1;

-- 2. Balance check: SUM(debit) - SUM(credit) per header should be 0 except
-- the seeded 'unbalanced' rows.
SELECT
    (bal.imbalance <> 0)                              AS is_unbalanced,
    COUNT(*)                                           AS n_headers
FROM (
    SELECT header_id, ROUND(SUM(debit_amount) - SUM(credit_amount), 2) AS imbalance
    FROM journal_line
    GROUP BY header_id
) bal
GROUP BY 1;
-- expect "true" count to match: SELECT COUNT(*) FROM ground_truth WHERE error_type = 'unbalanced';

-- 3. ground_truth by error_type x detectability.
SELECT error_type, detectability, COUNT(*) AS n
FROM ground_truth
GROUP BY error_type, detectability
ORDER BY error_type, detectability;

-- 4. Amount distribution (right-skew check: mean >> median).
SELECT
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY debit_amount) AS median_debit,
    AVG(debit_amount)                                          AS mean_debit,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY debit_amount) AS p95_debit,
    MAX(debit_amount)                                          AS max_debit
FROM journal_line
WHERE debit_amount > 0;

-- 5. Posting-hour concentration (08:00-18:00 core window).
SELECT
    ROUND(100.0 * COUNT(*) FILTER (WHERE EXTRACT(HOUR FROM posting_datetime) BETWEEN 8 AND 18)
          / COUNT(*), 1) AS pct_business_hours
FROM journal_header;

-- 6. NEW - transaction_date lag distribution (posting - transaction), days.
-- Baseline (non-anomalous) lag should cluster at 0-2 days; the seeded
-- 'backdated' errors are what stretch the tail past ~10 days.
SELECT
    CASE
        WHEN lag_days <= 2  THEN '0-2  (baseline / hard)'
        WHEN lag_days <= 9  THEN '3-9  (medium)'
        ELSE '10+  (easy)'
    END AS lag_bucket,
    MIN(lag_days) AS min_lag,
    MAX(lag_days) AS max_lag,
    COUNT(*) AS n_headers,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM (
    SELECT posting_datetime::date - transaction_date AS lag_days
    FROM journal_header
) lags
GROUP BY 1
ORDER BY min_lag;

-- 7. NEW - unmatched_bank ground truth, split by direction.
SELECT
    CASE WHEN bank_txn_id IS NULL THEN 'ledger_side_orphan (no bank line)'
         ELSE 'bank_side_orphan (phantom bank row)'
    END AS orphan_direction,
    detectability,
    COUNT(*) AS n
FROM ground_truth
WHERE error_type = 'unmatched_bank'
GROUP BY 1, 2
ORDER BY 1, 2;
