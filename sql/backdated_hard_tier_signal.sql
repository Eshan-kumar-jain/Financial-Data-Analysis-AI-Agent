-- ============================================================================
-- backdated_hard_tier_signal.sql
-- Question: does the 'hard' detectability tier of seeded 'backdated' errors
-- carry ANY signal beyond transaction_date lag, or is it indistinguishable
-- from clean entries on every other axis?
--
-- Groups compared:
--   clean            = headers with zero ground_truth rows at all
--   backdated / hard, medium, easy = seeded backdated headers, split by the
--     detectability the generator assigned (lag<3 / 3-9 / >=10 days,
--     see data/generate_journal_entries.py::seed_backdated_errors)
--
-- Metrics, all computed without touching ground_truth as a feature -
-- ground_truth is only used here to LABEL the groups for comparison, never
-- joined into anything that would become a model input (see CLAUDE.md
-- Hard rules).
-- ============================================================================

WITH period_end AS (
    -- Last calendar day per fiscal_period, used as the period-end anchor
    -- for days_from_period_end. (Not is_month_end's last-3-business-days
    -- flag - we want a single anchor date per period, not a window.)
    SELECT fiscal_period, MAX(calendar_date) AS period_end_date
    FROM dim_date
    GROUP BY fiscal_period
),

header_calc AS (
    SELECT
        h.header_id,
        h.source_system,
        h.employee_key,
        h.posting_datetime,
        e.typical_start_hour,
        e.typical_end_hour,
        pe.period_end_date - d.calendar_date AS days_from_period_end,
        -- after-hours = posting hour outside the poster's own typical
        -- start/end window (same definition the generator uses to seed
        -- off_hours_posting, rather than a fixed 08-18 cutoff)
        (EXTRACT(HOUR FROM h.posting_datetime) < e.typical_start_hour
         OR EXTRACT(HOUR FROM h.posting_datetime) >= e.typical_end_hour) AS is_after_hours
    FROM journal_header h
    JOIN dim_employee e ON e.employee_key = h.employee_key
    JOIN dim_date d ON d.date_key = h.date_key
    JOIN period_end pe ON pe.fiscal_period = d.fiscal_period
),

-- First time each employee posts to each account, at line grain.
account_first_touch AS (
    SELECT
        l.header_id,
        h.employee_key,
        l.account_key,
        h.posting_datetime,
        MIN(h.posting_datetime) OVER (PARTITION BY h.employee_key, l.account_key) AS first_touch_dt
    FROM journal_line l
    JOIN journal_header h ON h.header_id = l.header_id
),

-- Header-level flag: TRUE if ANY line on the header is that employee's
-- first-ever posting to that account.
header_new_account AS (
    SELECT header_id, BOOL_OR(posting_datetime = first_touch_dt) AS has_new_account_pairing
    FROM account_first_touch
    GROUP BY header_id
),

labeled AS (
    SELECT
        hc.header_id,
        hc.source_system,
        hc.is_after_hours,
        hc.days_from_period_end,
        hna.has_new_account_pairing,
        CASE
            WHEN gt.error_type = 'backdated' THEN 'backdated_' || gt.detectability
            WHEN gt_any.header_id IS NOT NULL THEN NULL  -- other seeded error type, exclude from baseline
            ELSE 'clean'
        END AS grp
    FROM header_calc hc
    JOIN header_new_account hna ON hna.header_id = hc.header_id
    LEFT JOIN ground_truth gt
        ON gt.header_id = hc.header_id AND gt.error_type = 'backdated'
    LEFT JOIN ground_truth gt_any
        ON gt_any.header_id = hc.header_id
)

SELECT
    grp,
    COUNT(*) AS n,
    ROUND(100.0 * COUNT(*) FILTER (WHERE source_system = 'GL_MANUAL') / COUNT(*), 1) AS pct_manual_source,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_after_hours) / COUNT(*), 1) AS pct_after_hours,
    ROUND(AVG(days_from_period_end), 2) AS mean_days_from_period_end,
    ROUND(100.0 * COUNT(*) FILTER (WHERE has_new_account_pairing) / COUNT(*), 1) AS pct_new_account_pairing
FROM labeled
WHERE grp IS NOT NULL
GROUP BY grp
ORDER BY CASE grp WHEN 'clean' THEN 0 WHEN 'backdated_hard' THEN 1
                   WHEN 'backdated_medium' THEN 2 WHEN 'backdated_easy' THEN 3 END;
