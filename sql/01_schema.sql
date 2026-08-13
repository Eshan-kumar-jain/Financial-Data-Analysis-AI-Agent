-- ============================================================================
-- 01_schema.sql
-- Phase 1: star schema for synthetic journal entry data.
--
-- Design choice: star schema, not a single flat table.
--   - dim_account / dim_employee / dim_date are dimensions: small, mostly
--     static, describe "who/what/when". Power BI relationships and DAX
--     measures assume this shape (one-to-many from dim -> fact), so building
--     it this way now avoids a rework later when the .pbix is built.
--   - journal_header / journal_line are the fact tables. Journal entries are
--     naturally two-grain: a header (one transaction, one posting event,
--     one preparer) and lines underneath it (each hits exactly one account,
--     as a debit or a credit). Modelling both grains separately means a
--     "3-line entry" isn't forced into a wide/awkward row, and balance
--     checks (SUM(debit) = SUM(credit) per header) are simple aggregates.
--   - ground_truth is deliberately NOT a column on the fact tables. It is a
--     separate table with FKs pointing at header_id/line_id. This is a hard
--     rule from CLAUDE.md: ground truth is label data seeded by the
--     generator, and joining it into a feature table would leak the answer
--     into the model. Keeping it a separate, one-directional FK relationship
--     makes that leakage impossible to do by accident (a feature-table build
--     that never references ground_truth simply cannot see it).
-- ============================================================================

DROP TABLE IF EXISTS ground_truth CASCADE;
DROP TABLE IF EXISTS journal_line CASCADE;
DROP TABLE IF EXISTS journal_header CASCADE;
DROP TABLE IF EXISTS dim_employee CASCADE;
DROP TABLE IF EXISTS dim_account CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

-- ----------------------------------------------------------------------------
-- dim_account: chart of accounts.
-- normal_balance drives sign conventions later (e.g. an Expense account with
-- a large credit balance is itself a mild anomaly signal). is_cash_account
-- flags accounts used for structuring-style anomalies (splitting a large
-- cash-affecting entry into several smaller ones to dodge an approval
-- threshold only makes sense for cash/AP-type accounts).
-- ----------------------------------------------------------------------------
CREATE TABLE dim_account (
    account_key      SERIAL PRIMARY KEY,
    account_id       VARCHAR(10)  NOT NULL UNIQUE,   -- e.g. '1000'
    account_name     VARCHAR(100) NOT NULL,
    account_type     VARCHAR(20)  NOT NULL
                      CHECK (account_type IN ('Asset','Liability','Equity','Revenue','Expense')),
    normal_balance   VARCHAR(6)   NOT NULL
                      CHECK (normal_balance IN ('Debit','Credit')),
    is_cash_account  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- ----------------------------------------------------------------------------
-- dim_employee: who is allowed to post journal entries.
-- typical_start_hour/typical_end_hour encode each employee's normal working
-- window. The generator uses this to decide what "off-hours for THIS
-- employee" means, which is a stronger anomaly signal than a single global
-- 08:00-18:00 cutoff (a controller who always closes the books at 21:00 is
-- not anomalous at 21:00; a junior AP clerk posting at 02:00 is).
-- ----------------------------------------------------------------------------
CREATE TABLE dim_employee (
    employee_key        SERIAL PRIMARY KEY,
    employee_id         VARCHAR(10) NOT NULL UNIQUE,   -- e.g. 'EMP0007'
    employee_name        VARCHAR(100) NOT NULL,
    department           VARCHAR(50) NOT NULL,
    role                  VARCHAR(30) NOT NULL,
    seniority_level       VARCHAR(10) NOT NULL
                          CHECK (seniority_level IN ('junior','mid','senior')),
    typical_start_hour    SMALLINT NOT NULL CHECK (typical_start_hour BETWEEN 0 AND 23),
    typical_end_hour      SMALLINT NOT NULL CHECK (typical_end_hour BETWEEN 0 AND 23)
);

-- ----------------------------------------------------------------------------
-- dim_date: standard Power BI date dimension, one row per calendar day for
-- the full 24-month window. Built once so DAX time intelligence
-- (month-over-month, MTD, etc.) has a marked date table to relate to,
-- and so "is this a weekend / month-end posting" is a lookup, not a
-- recomputed EXTRACT() in every query.
-- ----------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_key       INT PRIMARY KEY,          -- YYYYMMDD, sortable int surrogate key
    calendar_date  DATE NOT NULL UNIQUE,
    year           SMALLINT NOT NULL,
    month          SMALLINT NOT NULL,
    day            SMALLINT NOT NULL,
    day_of_week    SMALLINT NOT NULL,        -- 0 = Monday ... 6 = Sunday
    day_name       VARCHAR(10) NOT NULL,
    is_weekend     BOOLEAN NOT NULL,
    is_month_end   BOOLEAN NOT NULL,         -- last 3 business days of the month
    fiscal_period  VARCHAR(7) NOT NULL       -- 'YYYY-MM', matches accounting close periods
);

-- ----------------------------------------------------------------------------
-- journal_header: one row per journal entry (the transaction), before it is
-- broken into its debit/credit lines.
--
-- reversal_flag / reversed_header_id: a later, separate correcting entry can
-- point back at the header it reverses. reversal_flag is TRUE only once that
-- correcting entry exists in the future - i.e. it is knowledge that isn't
-- available at posting time. CLAUDE.md flags this as retrospective for
-- exactly that reason: using it as a model feature would let the model see
-- into the future of its own training data. It stays label-adjacent
-- metadata, same spirit as ground_truth.
-- ----------------------------------------------------------------------------
CREATE TABLE journal_header (
    header_id           BIGSERIAL PRIMARY KEY,
    header_id_text       VARCHAR(20) NOT NULL UNIQUE,   -- human-facing JE number, e.g. 'JE-000001'
    posting_datetime     TIMESTAMP NOT NULL,
    date_key             INT NOT NULL REFERENCES dim_date(date_key),
    employee_key         INT NOT NULL REFERENCES dim_employee(employee_key),
    source_system        VARCHAR(20) NOT NULL,          -- e.g. 'AP', 'AR', 'GL_MANUAL', 'PAYROLL'
    entry_description    VARCHAR(200),
    is_reversal          BOOLEAN NOT NULL DEFAULT FALSE, -- this header IS a correcting entry
    reversed_header_id   BIGINT REFERENCES journal_header(header_id), -- points at what it corrects
    reversal_flag        BOOLEAN NOT NULL DEFAULT FALSE  -- this header WAS later reversed (retrospective, label-only)
);

CREATE INDEX ix_journal_header_date_key ON journal_header(date_key);
CREATE INDEX ix_journal_header_employee_key ON journal_header(employee_key);

-- ----------------------------------------------------------------------------
-- journal_line: one row per debit or credit line within a header.
-- Debit and credit are kept as separate NUMERIC columns (rather than one
-- signed "amount") because that's how a real general ledger stores lines,
-- and it's what makes the balance check an honest SUM rather than a
-- CASE WHEN reconstruction: SUM(debit_amount) - SUM(credit_amount) per
-- header should equal 0 for every well-formed entry, and one of the seeded
-- error types deliberately breaks that.
-- ----------------------------------------------------------------------------
CREATE TABLE journal_line (
    line_id           BIGSERIAL PRIMARY KEY,
    header_id         BIGINT NOT NULL REFERENCES journal_header(header_id),
    line_num          SMALLINT NOT NULL,
    account_key       INT NOT NULL REFERENCES dim_account(account_key),
    debit_amount      NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (debit_amount >= 0),
    credit_amount     NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    line_description  VARCHAR(200),
    UNIQUE (header_id, line_num)
);

CREATE INDEX ix_journal_line_header_id ON journal_line(header_id);
CREATE INDEX ix_journal_line_account_key ON journal_line(account_key);

-- ----------------------------------------------------------------------------
-- ground_truth: labels only, seeded by the /data generator. Never joined
-- into a feature table or used as a model input (CLAUDE.md hard rule).
-- header_id/line_id are both nullable because some error types are a
-- property of the whole entry (unbalanced, structuring) and others are a
-- property of a single line (round_number, unusual_account_pair) -
-- forcing everything to line grain would misrepresent header-level errors.
-- detectability is tracked explicitly so recall can be reported by
-- difficulty tier instead of one flat number - CLAUDE.md calls out that a
-- single recall figure over uniformly-easy errors is meaningless.
-- ----------------------------------------------------------------------------
CREATE TABLE ground_truth (
    gt_id           BIGSERIAL PRIMARY KEY,
    header_id       BIGINT REFERENCES journal_header(header_id),
    line_id         BIGINT REFERENCES journal_line(line_id),
    error_type      VARCHAR(30) NOT NULL
                    CHECK (error_type IN (
                        'duplicate',
                        'round_number',
                        'unbalanced',
                        'off_hours_posting',
                        'unusual_account_pair',
                        'structuring'
                    )),
    detectability   VARCHAR(10) NOT NULL
                    CHECK (detectability IN ('easy','medium','hard')),
    notes           VARCHAR(300),
    CHECK (header_id IS NOT NULL OR line_id IS NOT NULL)
);

CREATE INDEX ix_ground_truth_header_id ON ground_truth(header_id);
CREATE INDEX ix_ground_truth_line_id ON ground_truth(line_id);
CREATE INDEX ix_ground_truth_error_type ON ground_truth(error_type);
