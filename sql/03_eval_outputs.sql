-- =====================================================================
-- Phase 5 -> Phase 6 handoff: evaluation outputs as Postgres tables.
--
-- notebooks/05_evaluation.ipynb computes every number in Phase 5 in
-- memory. Power BI cannot read a notebook variable, and CLAUDE.md rules
-- out an intermediate CSV, so Section 10 of that notebook writes its
-- results back here. This file is the DDL only - the notebook fills the
-- tables, and re-running this file resets them.
--
-- LABEL WARNING. eval_entry.is_error, eval_entry_label and every
-- precision/recall column below are derived from ground_truth. These are
-- REPORTING tables: they exist so a dashboard can show how well the
-- detection methods did. They must never be joined into
-- journal_entry_features or used as a model input - that is the same
-- leakage the Hard rules ban for ground_truth itself, and routing the
-- labels through a differently-named table does not change what they are.
--
-- Grain summary:
--   eval_method           one row per detection method            (~14)
--   eval_entry            one row per TEST-period header          (28,695)
--   eval_entry_label      one row per (header, error_type) pair   (~1,297)
--   eval_entry_flag       one row per (header, method)            (~370k)
--   eval_method_score     one row per (method, slice)             (~250)
--   eval_threshold_sweep  one row per threshold on the grid       (99)
-- =====================================================================

DROP TABLE IF EXISTS eval_threshold_sweep CASCADE;
DROP TABLE IF EXISTS eval_method_score     CASCADE;
DROP TABLE IF EXISTS eval_entry_flag       CASCADE;
DROP TABLE IF EXISTS eval_entry_label      CASCADE;
DROP TABLE IF EXISTS eval_entry            CASCADE;
DROP TABLE IF EXISTS eval_method           CASCADE;

-- ---------------------------------------------------------------------
-- eval_method - the method dimension every other eval table points at.
-- One place that says what a method is, so Power BI can slice by family
-- (blind method vs. supervised model vs. deterministic layer) without a
-- DAX SWITCH listing method names by hand.
-- ---------------------------------------------------------------------
CREATE TABLE eval_method (
    method_name     VARCHAR(60)  PRIMARY KEY,
    method_family   VARCHAR(20)  NOT NULL
                    CHECK (method_family IN ('unsupervised', 'supervised',
                                             'layer', 'combined', 'final')),
    scoreboard      CHAR(1)      NULL
                    CHECK (scoreboard IN ('A', 'B')),   -- NULL: layers/combos sit on neither
    threshold       NUMERIC(5,4) NULL,                  -- NULL where the flag is a rule, not a cut
    produces_score  BOOLEAN      NOT NULL,              -- FALSE -> eval_entry_flag.score is NULL
    sort_order      SMALLINT     NOT NULL,
    method_note     VARCHAR(300) NULL
);

-- ---------------------------------------------------------------------
-- eval_entry - one row per test-period header (the evaluation
-- population). Narrow on purpose: everything descriptive already lives
-- in journal_header / dim_date / dim_employee, and header_id joins to it.
-- ---------------------------------------------------------------------
CREATE TABLE eval_entry (
    header_id      BIGINT        PRIMARY KEY REFERENCES journal_header(header_id),
    fiscal_period  VARCHAR(7)    NOT NULL,
    date_key       INT           NOT NULL REFERENCES dim_date(date_key),
    employee_key   INT           NOT NULL REFERENCES dim_employee(employee_key),
    total_amount   NUMERIC(14,2) NOT NULL,
    is_error       BOOLEAN       NOT NULL,   -- label, reporting only (see LABEL WARNING)
    n_labels       SMALLINT      NOT NULL,   -- 0, 1 or 2 - the doubly-labelled headers
    final_flag     BOOLEAN       NOT NULL,   -- the final layered system's verdict
    final_outcome  VARCHAR(2)    NOT NULL
                   CHECK (final_outcome IN ('TP', 'FP', 'FN', 'TN'))
);

CREATE INDEX idx_eval_entry_period ON eval_entry (fiscal_period);

-- ---------------------------------------------------------------------
-- eval_entry_label - the pair grain. A header carrying two error types
-- gets two rows, which is what makes per-type recall a PAIR-level number
-- rather than a DISTINCTCOUNT over headers (Section 9 of the notebook
-- insists on this: a header-level count would silently drop one of the
-- two types for the ~18 doubly-labelled headers).
-- ---------------------------------------------------------------------
CREATE TABLE eval_entry_label (
    header_id      BIGINT      NOT NULL REFERENCES eval_entry(header_id),
    error_type     VARCHAR(30) NOT NULL,
    detectability  VARCHAR(10) NOT NULL
                   CHECK (detectability IN ('easy', 'medium', 'hard')),
    PRIMARY KEY (header_id, error_type)
);

CREATE INDEX idx_eval_entry_label_type ON eval_entry_label (error_type, detectability);

-- ---------------------------------------------------------------------
-- eval_entry_flag - the wide-to-long fact. Every method's verdict on
-- every test header, one row each. Long rather than one column per
-- method so a single DAX measure serves all of them behind a slicer.
--
-- score is DOUBLE PRECISION and is NOT comparable across methods: it is
-- a MAD in percentage points for Benford, |z| for the z-score, IQRs past
-- the fence for IQR, a negated decision_function for Isolation Forest
-- and a probability for the three models. Rank or threshold within a
-- method; never average across them.
--
-- is_covered FALSE means the method could not see this header at all
-- (no testable primary account, fewer than 10 prior entries on the
-- account, no cash line). Those rows carry is_flagged FALSE and a NULL
-- score - a method gets no credit and no blame outside its coverage.
-- ---------------------------------------------------------------------
CREATE TABLE eval_entry_flag (
    header_id    BIGINT      NOT NULL REFERENCES eval_entry(header_id),
    method_name  VARCHAR(60) NOT NULL REFERENCES eval_method(method_name),
    is_flagged   BOOLEAN     NOT NULL,
    is_covered   BOOLEAN     NOT NULL,
    score        DOUBLE PRECISION NULL,
    outcome      VARCHAR(2)  NOT NULL
                 CHECK (outcome IN ('TP', 'FP', 'FN', 'TN')),
    PRIMARY KEY (header_id, method_name)
);

CREATE INDEX idx_eval_entry_flag_method ON eval_entry_flag (method_name, is_flagged);

-- ---------------------------------------------------------------------
-- eval_method_score - the scoreboard, long by slice.
--
-- slice_type tells you which denominator a row uses, and that is the
-- whole point of the table:
--   'overall'        header-level, all 28,695 test headers. precision,
--                    recall, f1, average_precision, coverage all valid.
--   'error_type'     PAIR-level recall over eval_entry_label rows of
--                    that type. n_pairs is the denominator.
--   'detectability'  same, sliced by tier.
--   'addressability' same, split into the types a method has a direct
--                    signal for vs. the ones it does not.
--
-- precision, f1 and average_precision are NULL on every non-'overall'
-- row, on purpose: a false positive has no error_type, so there is no
-- honest per-type precision denominator. precision_vs_slice is the one
-- per-type precision that IS defined - true positives of this type over
-- the method's TOTAL flags - and it is the number the notebook uses to
-- score a targeted layer (a balance check that flags 76 headers, all 76
-- unbalanced, scores 1.000 here). It is deliberately harsh on a
-- general-purpose method, which is why it sits in its own column rather
-- than in 'precision'.
-- ---------------------------------------------------------------------
CREATE TABLE eval_method_score (
    eval_score_id           BIGSERIAL    PRIMARY KEY,
    method_name             VARCHAR(60)  NOT NULL REFERENCES eval_method(method_name),
    slice_type              VARCHAR(15)  NOT NULL
                            CHECK (slice_type IN ('overall', 'error_type',
                                                  'detectability', 'addressability')),
    slice_value             VARCHAR(30)  NOT NULL,   -- 'all' on the 'overall' rows
    n_pairs                 INT          NOT NULL,   -- positives in the slice (recall denominator)
    n_flagged               INT          NOT NULL,   -- the method's total flags over all test headers
    true_positives          INT          NOT NULL,
    false_positives         INT          NULL,       -- header-level only
    false_negatives         INT          NULL,       -- header-level only
    precision               DOUBLE PRECISION NULL,
    recall                  DOUBLE PRECISION NOT NULL,
    f1                      DOUBLE PRECISION NULL,
    average_precision       DOUBLE PRECISION NULL,
    coverage                DOUBLE PRECISION NULL,
    flag_rate               DOUBLE PRECISION NOT NULL,
    precision_vs_slice      DOUBLE PRECISION NULL,
    analyst_hours_per_month DOUBLE PRECISION NULL,   -- fp * 5 min / 60 / 6, header-level only
    UNIQUE (method_name, slice_type, slice_value)
);

-- ---------------------------------------------------------------------
-- eval_threshold_sweep - the cost curve behind the operating point.
-- One row per threshold on the notebook's 0.01..0.99 grid for the chosen
-- model, so the dashboard can show WHY 0.57 rather than assert it.
-- is_chosen marks the cost-minimising threshold at the 19:1 ratio;
-- cost_fn_fp_ratio is stored on every row because the ratio is an
-- assumption, and a reader who disagrees with it should be able to see
-- which one produced the pick.
-- ---------------------------------------------------------------------
CREATE TABLE eval_threshold_sweep (
    threshold               NUMERIC(4,2) PRIMARY KEY,
    model_name              VARCHAR(60)  NOT NULL REFERENCES eval_method(method_name),
    flagged                 INT          NOT NULL,
    true_positives          INT          NOT NULL,
    false_positives         INT          NOT NULL,
    false_negatives         INT          NOT NULL,
    precision               DOUBLE PRECISION NOT NULL,
    recall                  DOUBLE PRECISION NOT NULL,
    f1                      DOUBLE PRECISION NOT NULL,
    flag_rate               DOUBLE PRECISION NOT NULL,
    analyst_hours_per_month DOUBLE PRECISION NOT NULL,
    expected_cost_minutes   DOUBLE PRECISION NOT NULL,  -- fp + ratio * fn, in FP-equivalents
    cost_fn_fp_ratio        DOUBLE PRECISION NOT NULL,
    is_chosen               BOOLEAN      NOT NULL
);
