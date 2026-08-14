"""
generate_journal_entries.py

Phase 1 synthetic data generator for the financial anomaly detection project.

What this does, and why it's a script and not a notebook:
  - It's a one-shot ETL job (generate -> load), not analysis. CLAUDE.md keeps
    notebooks narrative for the EDA/methods/evaluation work; this is
    infrastructure that runs once (or re-runs when the shape needs tuning).
  - Postgres is the single source of truth (CLAUDE.md hard rule), so this
    writes directly to Postgres via COPY. No intermediate CSVs are produced.
  - Connection comes entirely from libpq environment variables
    (PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD) - psycopg2.connect() with no
    arguments reads them automatically. Nothing is hardcoded here. .env
    (gitignored, see .env.example) is loaded via python-dotenv so those
    vars don't have to be exported in every shell.

Run:
    python generate_journal_entries.py            # generate + load to Postgres
    python generate_journal_entries.py --dry-run  # generate + validate only, no DB

Distribution choices are explained inline as comments, next to the code that
implements them, per CLAUDE.md style rules.
"""

import argparse
import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    psycopg2 = None

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ============================================================================
# Config
# ============================================================================
RNG_SEED = 42
START_DATE = date(2024, 1, 1)
NUM_MONTHS = 24
TARGET_LINES = 250_000
ERROR_RATE = 0.03          # ~3% of journal entries (headers) are seeded errors
N_EMPLOYEES = 40
N_ACCOUNTS = 27

# backdated / bank-feed error rates, same 0.003-0.009 scale as ERROR_MIX below.
# Kept separate from ERROR_MIX because they don't seed off the same
# header-pool mechanism (backdated overrides a column rather than mutating a
# header, unmatched_bank operates on bank_transactions rows, not headers).
BACKDATED_RATE = 0.006
UNMATCHED_LEDGER_RATE = 0.006   # fraction of ledger cash lines with no bank counterpart
UNMATCHED_PHANTOM_RATE = 0.006  # phantom bank rows, as a fraction of cash-line count

rng = np.random.default_rng(RNG_SEED)


# ============================================================================
# Dimension: dim_date
# ============================================================================
def build_dim_date(start: date, num_months: int) -> pd.DataFrame:
    """One row per calendar day. is_month_end marks the last 3 BUSINESS days
    of each month, because that's the window where real accounting close
    activity (accruals, depreciation, cutoff-driven AP pushes) concentrates -
    not just the calendar-last day."""
    end = (pd.Timestamp(start) + pd.DateOffset(months=num_months)) - pd.Timedelta(days=1)
    dates = pd.date_range(start, end, freq="D")

    df = pd.DataFrame({"calendar_date": dates})
    df["date_key"] = df["calendar_date"].dt.strftime("%Y%m%d").astype(int)
    df["year"] = df["calendar_date"].dt.year
    df["month"] = df["calendar_date"].dt.month
    df["day"] = df["calendar_date"].dt.day
    df["day_of_week"] = df["calendar_date"].dt.dayofweek  # 0=Mon
    df["day_name"] = df["calendar_date"].dt.day_name()
    df["is_weekend"] = df["day_of_week"] >= 5
    df["fiscal_period"] = df["calendar_date"].dt.strftime("%Y-%m")

    # Last 3 business (Mon-Fri) days of each month.
    df["is_month_end"] = False
    for _, grp in df.groupby(["year", "month"]):
        business_days = grp[~grp["is_weekend"]].sort_values("calendar_date")
        last_3 = business_days.tail(3).index
        df.loc[last_3, "is_month_end"] = True

    return df[["date_key", "calendar_date", "year", "month", "day", "day_of_week",
               "day_name", "is_weekend", "is_month_end", "fiscal_period"]]


# ============================================================================
# Dimension: dim_account
# ============================================================================
def build_dim_account() -> pd.DataFrame:
    """Small, fixed chart of accounts. normal_balance and is_cash_account are
    used later to build realistic debit/credit pairings and to decide which
    accounts can plausibly be a structuring target (cash/AP accounts, where
    someone splitting a payment to dodge an approval threshold makes sense)."""
    accounts = [
        # (account_id, name, type, normal_balance, is_cash)
        ("1000", "Cash - Operating",        "Asset",     "Debit",  True),
        ("1010", "Cash - Payroll",          "Asset",     "Debit",  True),
        ("1100", "Accounts Receivable",     "Asset",     "Debit",  False),
        ("1200", "Inventory",               "Asset",     "Debit",  False),
        ("1300", "Prepaid Expenses",        "Asset",     "Debit",  False),
        ("1500", "Fixed Assets",            "Asset",     "Debit",  False),
        ("1510", "Accumulated Depreciation","Asset",     "Credit", False),
        ("2000", "Accounts Payable",        "Liability", "Credit", False),
        ("2100", "Accrued Liabilities",     "Liability", "Credit", False),
        ("2200", "Payroll Liabilities",     "Liability", "Credit", False),
        ("2300", "Unearned Revenue",        "Liability", "Credit", False),
        ("2400", "Notes Payable",           "Liability", "Credit", False),
        ("3000", "Common Stock",            "Equity",    "Credit", False),
        ("3100", "Retained Earnings",       "Equity",    "Credit", False),
        ("4000", "Product Revenue",         "Revenue",   "Credit", False),
        ("4100", "Service Revenue",         "Revenue",   "Credit", False),
        ("4200", "Interest Income",         "Revenue",   "Credit", False),
        ("5000", "Cost of Goods Sold",      "Expense",   "Debit",  False),
        ("5100", "Salaries Expense",        "Expense",   "Debit",  False),
        ("5200", "Rent Expense",            "Expense",   "Debit",  False),
        ("5300", "Utilities Expense",       "Expense",   "Debit",  False),
        ("5400", "Marketing Expense",       "Expense",   "Debit",  False),
        ("5500", "Travel Expense",          "Expense",   "Debit",  False),
        ("5600", "Office Supplies Expense", "Expense",   "Debit",  False),
        ("5700", "Depreciation Expense",    "Expense",   "Debit",  False),
        ("5800", "Professional Fees",       "Expense",   "Debit",  False),
        ("5900", "Interest Expense",        "Expense",   "Debit",  False),
    ]
    assert len(accounts) == N_ACCOUNTS
    df = pd.DataFrame(accounts, columns=[
        "account_id", "account_name", "account_type", "normal_balance", "is_cash_account"
    ])
    df.insert(0, "account_key", range(1, len(df) + 1))
    return df


# ============================================================================
# Dimension: dim_employee
# ============================================================================
def build_dim_employee() -> pd.DataFrame:
    """40 employees across the departments that actually touch a GL.
    typical_start_hour/typical_end_hour matter more than a job title: they
    define what "off-hours FOR THIS PERSON" means, so a controller who
    normally closes the books at 20:00 isn't flagged just for working late.
    A few employees are deliberately given wide/late windows (senior roles,
    one FP&A night-owl) so the natural after-hours tail has a legitimate
    explanation for part of its mass, not just noise."""
    departments = ["AP", "AR", "GL", "Payroll", "Treasury", "FP&A"]
    roles_by_dept = {
        "AP": ["AP Clerk", "AP Clerk", "AP Supervisor"],
        "AR": ["AR Clerk", "AR Clerk", "AR Supervisor"],
        "GL": ["Staff Accountant", "Senior Accountant", "Controller"],
        "Payroll": ["Payroll Clerk", "Payroll Manager"],
        "Treasury": ["Treasury Analyst"],
        "FP&A": ["FP&A Analyst", "FP&A Manager"],
    }
    first_names = ["Alex","Jordan","Sam","Taylor","Morgan","Casey","Riley","Jamie",
                   "Drew","Cameron","Avery","Quinn","Reese","Rowan","Skyler","Dakota",
                   "Emerson","Finley","Hayden","Peyton","Kendall","Logan","Micah","Parker",
                   "Sawyer","Elliot","Blair","Charlie","Devon","Frankie","Harper","Jesse",
                   "Kai","Lane","Marley","Noel","Oakley","Remy","Shay","Tatum"]
    last_names = ["Chen","Patel","Garcia","Kim","Nguyen","Smith","Johnson","Brown",
                  "Davis","Rodriguez","Martinez","Lopez","Wilson","Anderson","Thomas",
                  "Taylor","Moore","Jackson","Martin","Lee","Perez","White","Harris",
                  "Clark","Lewis","Young","Walker","Hall","Allen","King","Wright",
                  "Scott","Green","Baker","Adams","Nelson","Hill","Ramirez","Campbell","Mitchell"]

    rows = []
    for i in range(N_EMPLOYEES):
        dept = departments[i % len(departments)]
        role = rng.choice(roles_by_dept[dept])
        is_senior = "Manager" in role or "Supervisor" in role or "Controller" in role or "Senior" in role
        seniority = "senior" if is_senior else rng.choice(["junior", "mid"], p=[0.4, 0.6])

        # Most staff: 08:00-17:00 or 09:00-18:00. Seniors skew later-ending.
        # A small minority (~10%) get a wide/late window - these are the
        # legitimately-late workers that explain part of the natural
        # after-hours tail without being anomalies.
        if rng.random() < 0.10:
            start_hour, end_hour = int(rng.choice([9, 10])), int(rng.choice([19, 20]))
        elif seniority == "senior":
            start_hour, end_hour = 9, 19
        else:
            start_hour, end_hour = int(rng.choice([8, 9])), int(rng.choice([17, 18]))

        rows.append({
            "employee_id": f"EMP{i+1:04d}",
            "employee_name": f"{first_names[i % len(first_names)]} {last_names[i % len(last_names)]}",
            "department": dept,
            "role": role,
            "seniority_level": seniority,
            "typical_start_hour": start_hour,
            "typical_end_hour": end_hour,
        })

    df = pd.DataFrame(rows)
    df.insert(0, "employee_key", range(1, len(df) + 1))
    return df


# ============================================================================
# Transaction categories: drive account pairing, amount distribution, and
# how volume is spread across the month. This is the main lever for
# realism - real GL activity isn't "pick 2 random accounts", it's a
# repeated set of business processes with their own rhythm.
# ============================================================================
CATEGORIES = {
    # name: (weight, debit_pool, credit_pool, lognormal mu, lognormal sigma,
    #        month_end_only, departments)
    "vendor_bill":     dict(weight=0.28, debit=["5000","5200","5300","5400","5500","5600","5800","1200"],
                             credit=["2000"], mu=6.0, sigma=1.1, month_end_only=False, dept="AP"),
    "vendor_payment":  dict(weight=0.20, debit=["2000"], credit=["1000"],
                             mu=6.0, sigma=1.0, month_end_only=False, dept="AP"),
    "sales_revenue":   dict(weight=0.18, debit=["1100"], credit=["4000","4100"],
                             mu=6.5, sigma=1.3, month_end_only=False, dept="AR"),
    "cash_receipt":    dict(weight=0.14, debit=["1000"], credit=["1100"],
                             mu=6.5, sigma=1.2, month_end_only=False, dept="AR"),
    "payroll_run":     dict(weight=0.06, debit=["5100"], credit=["2200","1010"],
                             mu=9.0, sigma=0.4, month_end_only=False, dept="Payroll"),
    "month_end_close": dict(weight=0.08, debit=["5700","5200","5300","5900"], credit=["1510","2100","1300"],
                             mu=7.0, sigma=1.0, month_end_only=True, dept="GL"),
    "manual_gl":       dict(weight=0.06, debit=list(), credit=list(),  # filled from full account list below
                             mu=7.0, sigma=1.8, month_end_only=False, dept="GL"),
}


# ============================================================================
# Bank feed: counterparty pools and reference-messifying.
# The bank feed mirrors ledger cash movements (see generate_bank_transactions)
# but is generated as text a real payment rail would produce - abbreviated,
# inconsistently cased, sometimes truncated or missing the JE number entirely.
# That's deliberate: CLAUDE.md's Python tool rules call for rapidfuzz fuzzy
# matching later, and an exact-match join against these references should
# fail on most rows, or the fuzzy-matching step would have nothing to do.
# ============================================================================
VENDOR_NAMES = [
    "Acme Supplies Ltd", "Globex Materials", "Initech Consulting",
    "Umbrella Logistics", "Stark Industrial", "Wayne Facilities Group",
    "Wonka Packaging Co", "Hooli Office Solutions", "Soylent Catering",
    "Vandelay Import Export", "Massive Dynamic Supply", "Gringotts Business Services",
]
CUSTOMER_NAMES = [
    "Northwind Traders", "Contoso Retail", "Fabrikam Inc", "Tailspin Toys",
    "Adventure Works", "Blue Yonder Airlines", "Litware Systems",
    "Proseware Manufacturing", "City Power & Gas", "Trey Research",
]
PAYROLL_PROVIDER = "ADP Payroll Services"

# Recognizable words a bank feed would routinely abbreviate.
ABBREVIATIONS = {
    "PAYMENT": "PMT", "PAYROLL": "PYRL", "INVOICE": "INV", "SERVICES": "SVCS",
    "SUPPLIES": "SUPP", "RECEIPT": "RCPT", "TRANSFER": "TRF", "CUSTOMER": "CUST",
    "VENDOR": "VEND", "CORPORATION": "CORP", "LIMITED": "LTD", "COMPANY": "CO",
    "INTERNATIONAL": "INTL", "LOGISTICS": "LOG", "SOLUTIONS": "SOL",
    "CONSULTING": "CONSULT", "MATERIALS": "MATL", "INDUSTRIAL": "IND",
    "FACILITIES": "FAC", "PACKAGING": "PKG", "CATERING": "CATER",
    "IMPORT": "IMP", "EXPORT": "EXP", "MANAGEMENT": "MGMT",
}


def messify_reference(clean_ref: str, rng_local) -> str:
    """Turns a clean 'LABEL JE-000123 Counterparty Name' string into
    something a bank feed would actually produce: some words abbreviated,
    the JE number dropped fairly often (missing invoice/reference number),
    two adjacent words occasionally transposed, inconsistent casing, and
    sometimes truncated (field-length cutoff). Each transformation is
    independently randomized so the output isn't one fixed pattern an
    exact-match join could special-case around."""
    words = clean_ref.split()

    words = [ABBREVIATIONS.get(w.upper(), w) if rng_local.random() < 0.5 else w
             for w in words]

    if rng_local.random() < 0.45:
        words = [w for w in words if not w.upper().startswith("JE-")]

    if len(words) >= 2 and rng_local.random() < 0.15:
        i = int(rng_local.integers(0, len(words) - 1))
        words[i], words[i + 1] = words[i + 1], words[i]

    ref = " ".join(words)

    case_roll = rng_local.random()
    if case_roll < 0.35:
        ref = ref.upper()
    elif case_roll < 0.6:
        ref = ref.lower()
    elif case_roll < 0.8:
        ref = ref.title()
    # else: leave title/mixed as built - some real feed rows come through clean-ish

    if rng_local.random() < 0.4 and len(ref) > 10:
        cut = int(len(ref) * rng_local.uniform(0.6, 0.9))
        ref = ref[:cut].rstrip()

    return ref[:140]


def build_pairing_pools(accounts: pd.DataFrame) -> None:
    """manual_gl deliberately draws from the full chart of accounts rather
    than a fixed pool - real one-off adjusting entries touch whatever
    account needs correcting. This also seeds natural noise: a handful of
    legitimate manual entries will look like unusual account pairs by
    chance, which is intentional (a naive 'flag every rare pairing' rule
    should NOT get 100% precision on this dataset)."""
    all_ids = accounts["account_id"].tolist()
    CATEGORIES["manual_gl"]["debit"] = all_ids
    CATEGORIES["manual_gl"]["credit"] = all_ids


# ============================================================================
# Header/line generation
# ============================================================================
def sample_header_dates(dim_date: pd.DataFrame, n_headers: int) -> np.ndarray:
    """Weight each calendar day, then sample header dates from that
    distribution. Weekend weight is low but not zero (skeleton-crew /
    batch postings still happen). Month-end weight is boosted ~2.5x - that's
    the accounting close crunch (accruals, depreciation, cutoff-driven AP
    pushes) that produces the volume spike CLAUDE.md asks for. First
    business day of the month gets a smaller boost too, since prior-month
    accrual reversals typically post immediately on reopen."""
    w = np.where(dim_date["is_weekend"], 0.15, 1.0)
    w = w * np.where(dim_date["is_month_end"], 2.5, 1.0)

    first_bd_idx = dim_date.groupby(["year", "month"])["date_key"].transform(
        lambda s: s.index == s[~dim_date.loc[s.index, "is_weekend"]].index.min()
    )
    w = w * np.where(first_bd_idx.fillna(False), 1.3, 1.0)

    p = w / w.sum()
    return rng.choice(dim_date["date_key"].values, size=n_headers, p=p)


def sample_posting_hour(start_hour: int, end_hour: int) -> tuple:
    """92% of entries post within the employee's own business-hours window,
    using a Beta(2,2) shape (symmetric, peaked mid-window, tapering at the
    edges) rather than uniform - most people post mid-shift, not the instant
    they log in or the moment before they leave.
    The remaining 8% is the after-hours tail: weighted toward evening
    (18:00-23:00, someone catching up) over the dead of night, which is
    the realistic shape of a "small after-hours tail", not a uniform
    24-hour spread."""
    if rng.random() < 0.92:
        frac = rng.beta(2, 2)
        hour_float = start_hour + frac * (end_hour - start_hour)
    else:
        if rng.random() < 0.7:
            hour_float = rng.uniform(18, 23)
        else:
            hour_float = rng.uniform(0, 6)
    hour = int(hour_float) % 24
    minute = int(rng.uniform(0, 60))
    second = int(rng.uniform(0, 60))
    return hour, minute, second


def pick_employee(employees: pd.DataFrame, dept: str) -> pd.Series:
    """Posting volume per employee follows a power law, not uniform: a
    couple of AP/AR clerks process most of the routine volume, seniors post
    rarely but for larger/rarer transaction types. Implemented with a
    Zipf-shaped weight within each department's employee pool."""
    pool = employees[employees["department"] == dept]
    if pool.empty:
        pool = employees
    ranks = np.arange(1, len(pool) + 1)
    weights = 1.0 / ranks  # Zipf-like
    weights = weights / weights.sum()
    idx = rng.choice(pool.index.values, p=weights)
    return employees.loc[idx]


def split_amount(total: float, n: int) -> np.ndarray:
    """Split a total across n lines using a Dirichlet draw so the pieces sum
    exactly to `total` (no rounding drift feeding a false 'unbalanced'
    signal on entries that were never meant to be anomalous). alpha=4 keeps
    pieces reasonably even rather than one piece dominating."""
    if n == 1:
        return np.array([total])
    fracs = rng.dirichlet(alpha=np.full(n, 4.0))
    amounts = np.round(fracs * total, 2)
    # Fix rounding residue on the last line so the split still sums exactly.
    amounts[-1] += round(total - amounts.sum(), 2)
    return amounts


def generate_clean_data(dim_date: pd.DataFrame, accounts: pd.DataFrame, employees: pd.DataFrame):
    """Generates the full clean (error-free) population of headers and
    lines. Errors are seeded afterward as a separate, explicit pass -
    kept apart so the 'what does normal look like' logic never has to
    reason about anomalies while it's running."""
    build_pairing_pools(accounts)
    account_by_id = accounts.set_index("account_id")

    cat_names = list(CATEGORIES.keys())
    cat_weights = np.array([CATEGORIES[c]["weight"] for c in cat_names])
    cat_weights = cat_weights / cat_weights.sum()

    # avg ~2.25 lines/header lands ~250k lines from this many headers.
    avg_lines_per_header = 2.25
    n_headers = int(TARGET_LINES / avg_lines_per_header)

    header_dates = sample_header_dates(dim_date, n_headers)
    date_lookup = dim_date.set_index("date_key")["calendar_date"]

    headers, lines = [], []
    header_id = 1
    line_id = 1

    for i in range(n_headers):
        date_key = int(header_dates[i])
        cal_date = date_lookup.loc[date_key]
        is_month_end = bool(dim_date.loc[dim_date["date_key"] == date_key, "is_month_end"].iloc[0])

        # month_end_close entries should only actually land on month-end days
        avail_cats = cat_names if not is_month_end else cat_names
        weights = cat_weights.copy()
        if not is_month_end:
            # zero out month_end_close weight on non-month-end days, renormalize
            zero_idx = cat_names.index("month_end_close")
            weights[zero_idx] = 0
            weights = weights / weights.sum()

        cat_name = rng.choice(cat_names, p=weights)
        cat = CATEGORIES[cat_name]

        employee = pick_employee(employees, cat["dept"])
        hour, minute, second = sample_posting_hour(
            employee["typical_start_hour"], employee["typical_end_hour"]
        )
        posting_dt = datetime.combine(cal_date, datetime.min.time()) + timedelta(
            hours=hour, minutes=minute, seconds=second
        )

        # Total transaction amount: category-specific log-normal. Log-normal
        # is used (rather than e.g. gamma) because it's the standard model
        # for "many small multiplicative effects" processes like invoice
        # amounts, and because CLAUDE.md specifically calls for right-skew:
        # median = exp(mu), long right tail controlled by sigma.
        total_amount = float(np.round(rng.lognormal(cat["mu"], cat["sigma"]), 2))
        total_amount = max(total_amount, 1.00)

        # Line count: mostly 2 (simple debit/credit), occasionally more
        # (e.g. payroll splitting across several liability accounts).
        n_lines_roll = rng.random()
        if n_lines_roll < 0.85:
            n_debit, n_credit = 1, 1
        elif n_lines_roll < 0.97:
            # split whichever side the category allows multiple accounts on
            if len(cat["credit"]) > 1:
                n_debit, n_credit = 1, int(rng.integers(2, 4))
            else:
                n_debit, n_credit = int(rng.integers(2, 4)), 1
        else:
            n_debit, n_credit = int(rng.integers(2, 3)), int(rng.integers(2, 4))

        debit_accts = rng.choice(cat["debit"], size=n_debit, replace=True)
        credit_accts = rng.choice(cat["credit"], size=n_credit, replace=True)
        debit_splits = split_amount(total_amount, n_debit)
        credit_splits = split_amount(total_amount, n_credit)

        headers.append({
            "header_id": header_id,
            "header_id_text": f"JE-{header_id:06d}",
            "posting_datetime": posting_dt,
            "date_key": date_key,
            "employee_key": int(employee["employee_key"]),
            "source_system": cat["dept"] if cat_name != "manual_gl" else "GL_MANUAL",
            "entry_description": cat_name.replace("_", " ").title(),
            "is_reversal": False,
            "reversed_header_id": None,
            "reversal_flag": False,
        })

        line_num = 1
        for acct_id, amt in zip(debit_accts, debit_splits):
            lines.append({
                "line_id": line_id, "header_id": header_id, "line_num": line_num,
                "account_key": int(account_by_id.loc[acct_id, "account_key"]),
                "debit_amount": float(amt), "credit_amount": 0.0,
                "line_description": cat_name.replace("_", " ").title(),
            })
            line_id += 1
            line_num += 1
        for acct_id, amt in zip(credit_accts, credit_splits):
            lines.append({
                "line_id": line_id, "header_id": header_id, "line_num": line_num,
                "account_key": int(account_by_id.loc[acct_id, "account_key"]),
                "debit_amount": 0.0, "credit_amount": float(amt),
                "line_description": cat_name.replace("_", " ").title(),
            })
            line_id += 1
            line_num += 1

        header_id += 1

    headers_df = pd.DataFrame(headers)
    lines_df = pd.DataFrame(lines)

    # A slice (~4%) of vendor_payment / cash_receipt headers get a genuine
    # correcting reversal in a later period. This is separate from the
    # seeded ERROR types - reversals are a normal part of GL activity
    # (a wrong invoice gets rebooked), not anomalies. It exists so
    # reversal_flag has real signal to be retrospective ABOUT.
    reversible = headers_df[headers_df["entry_description"].isin(
        ["Vendor Payment", "Cash Receipt"])].sample(frac=0.04, random_state=RNG_SEED)
    headers_df, lines_df, header_id, line_id = _seed_reversals(
        headers_df, lines_df, reversible, header_id, line_id
    )
    # Same nullable-int shape as ground_truth's header_id/line_id below:
    # most rows have reversed_header_id = None, a few have a real id.
    headers_df["reversed_header_id"] = headers_df["reversed_header_id"].astype("Int64")

    return headers_df, lines_df, header_id, line_id, account_by_id, employees


def _seed_reversals(headers_df, lines_df, reversible, next_header_id, next_line_id):
    new_headers, new_lines = [], []
    for _, h in reversible.iterrows():
        orig_dt = h["posting_datetime"]
        rev_dt = orig_dt + timedelta(days=int(rng.integers(3, 30)))
        new_h = h.copy()
        new_h["header_id"] = next_header_id
        new_h["header_id_text"] = f"JE-{next_header_id:06d}"
        new_h["posting_datetime"] = rev_dt
        new_h["is_reversal"] = True
        new_h["reversed_header_id"] = h["header_id"]
        new_h["entry_description"] = f"Reversal of {h['header_id_text']}"
        new_headers.append(new_h)

        orig_lines = lines_df[lines_df["header_id"] == h["header_id"]]
        for _, l in orig_lines.iterrows():
            new_l = l.copy()
            new_l["line_id"] = next_line_id
            new_l["header_id"] = next_header_id
            # flip debit/credit to reverse the original entry
            new_l["debit_amount"], new_l["credit_amount"] = l["credit_amount"], l["debit_amount"]
            new_lines.append(new_l)
            next_line_id += 1
        headers_df.loc[headers_df["header_id"] == h["header_id"], "reversal_flag"] = True
        next_header_id += 1

    headers_df = pd.concat([headers_df, pd.DataFrame(new_headers)], ignore_index=True)
    lines_df = pd.concat([lines_df, pd.DataFrame(new_lines)], ignore_index=True)
    return headers_df, lines_df, next_header_id, next_line_id


# ============================================================================
# Error seeding - the 6 ground-truth error types.
# Proportions sum to ERROR_RATE (~3% of headers) and are deliberately spread
# across easy/medium/hard so recall isn't measured on a uniformly-easy set
# (CLAUDE.md hard rule).
# ============================================================================
ERROR_MIX = {
    "round_number":          0.009,  # easy-ish, but noisy (some legit round amounts exist)
    "unbalanced":            0.003,  # easy - a simple SUM(debit)-SUM(credit) window catches it
    "duplicate":             0.005,  # medium
    "off_hours_posting":     0.005,  # medium
    "unusual_account_pair":  0.005,  # medium/hard - needs a pairing model, not a fixed rule
    "structuring":           0.003,  # hard - each individual entry looks normal in isolation
}


def seed_errors(headers_df, lines_df, next_header_id, next_line_id, account_by_id, employees):
    rng_local = rng
    n_headers = len(headers_df)
    ground_truth = []

    # Pool of eligible header_ids to draw error targets from, sampled once
    # up front and disjoint per type so no entry gets double-mutated
    # (keeps each ground_truth label attributable to exactly one cause).
    # Also excludes headers that already HAVE a reversal pointing at them:
    # structuring deletes the original header outright when it splits it,
    # which would orphan that reversal's reversed_header_id FK.
    reversed_original_ids = headers_df["reversed_header_id"].dropna().unique()
    eligible = headers_df[
        (~headers_df["is_reversal"]) & (~headers_df["header_id"].isin(reversed_original_ids))
    ]["header_id"].to_numpy(copy=True)  # pandas CoW can hand back a read-only view; shuffle needs write access
    rng_local.shuffle(eligible)

    cursor = 0

    def take(frac):
        nonlocal cursor
        n = int(round(frac * n_headers))
        ids = eligible[cursor:cursor + n]
        cursor += n
        return ids

    # --- round_number: force a suspiciously round total amount -----------
    for hid in take(ERROR_MIX["round_number"]):
        hlines = lines_df[lines_df["header_id"] == hid]
        total = float(hlines["debit_amount"].sum())
        round_total = float(rng_local.choice([1000, 2500, 5000, 10000, 25000, 50000]))
        # Rescale every line proportionally so debit/credit stay balanced.
        # Rounding each line to a cent independently can leave a few cents of
        # drift when a side has multiple lines - push that residual onto the
        # side's last line so the header still balances exactly. Otherwise
        # this would silently create an unintended 'unbalanced' header that
        # ground_truth never labels as such (caught by the balance check in
        # validate() during the dry run).
        for side in ("debit_amount", "credit_amount"):
            mask = (lines_df["header_id"] == hid) & (lines_df[side] > 0)
            side_total = lines_df.loc[mask, side].sum()
            if side_total > 0:
                idx = lines_df.index[mask]
                new_vals = np.round(lines_df.loc[idx, side] / side_total * round_total, 2)
                new_vals.iloc[-1] += round(round_total - new_vals.sum(), 2)
                lines_df.loc[idx, side] = new_vals
        ground_truth.append(dict(header_id=hid, line_id=None, error_type="round_number",
                                  detectability="easy",
                                  notes=f"Amount forced to round figure {round_total:.0f}"))

    # --- unbalanced: nudge one line so debits != credits ------------------
    for hid in take(ERROR_MIX["unbalanced"]):
        hlines = lines_df[lines_df["header_id"] == hid]
        target_line = hlines.sample(1, random_state=int(hid)).iloc[0]
        delta = round(float(rng_local.uniform(0.5, 75.0)), 2)
        col = "debit_amount" if target_line["debit_amount"] > 0 else "credit_amount"
        lines_df.loc[lines_df["line_id"] == target_line["line_id"], col] += delta
        ground_truth.append(dict(header_id=hid, line_id=int(target_line["line_id"]),
                                  error_type="unbalanced", detectability="easy",
                                  notes=f"Line nudged by {delta:.2f}, header no longer balances"))

    # --- duplicate: clone an entry a few days later ------------------------
    for hid in take(ERROR_MIX["duplicate"]):
        orig_h = headers_df[headers_df["header_id"] == hid].iloc[0]
        orig_lines = lines_df[lines_df["header_id"] == hid]
        gap_days = int(rng_local.integers(0, 5))  # same-day dup (easy) .. few days later (harder)
        new_h = orig_h.copy()
        new_h["header_id"] = next_header_id
        new_h["header_id_text"] = f"JE-{next_header_id:06d}"
        new_h["posting_datetime"] = orig_h["posting_datetime"] + timedelta(days=gap_days)
        headers_df = pd.concat([headers_df, pd.DataFrame([new_h])], ignore_index=True)

        new_lines = []
        for _, l in orig_lines.iterrows():
            nl = l.copy()
            nl["line_id"] = next_line_id
            nl["header_id"] = next_header_id
            new_lines.append(nl)
            next_line_id += 1
        lines_df = pd.concat([lines_df, pd.DataFrame(new_lines)], ignore_index=True)

        ground_truth.append(dict(header_id=int(next_header_id), line_id=None,
                                  error_type="duplicate",
                                  detectability="easy" if gap_days == 0 else "medium",
                                  notes=f"Duplicate of {orig_h['header_id_text']}, {gap_days}d later"))
        next_header_id += 1

    # --- off_hours_posting: force a daytime-only employee to post 00:00-05:00
    daytime_employees = employees[employees["typical_start_hour"] <= 9]
    for hid in take(ERROR_MIX["off_hours_posting"]):
        emp = daytime_employees.sample(1, random_state=int(hid)).iloc[0]
        bad_hour = int(rng_local.integers(0, 5))
        orig_dt = headers_df.loc[headers_df["header_id"] == hid, "posting_datetime"].iloc[0]
        new_dt = orig_dt.replace(hour=bad_hour, minute=int(rng_local.integers(0, 60)))
        headers_df.loc[headers_df["header_id"] == hid, "posting_datetime"] = new_dt
        headers_df.loc[headers_df["header_id"] == hid, "employee_key"] = int(emp["employee_key"])
        ground_truth.append(dict(header_id=hid, line_id=None, error_type="off_hours_posting",
                                  detectability="medium",
                                  notes=f"{emp['employee_name']} (typical {emp['typical_start_hour']}-{emp['typical_end_hour']}) posted at {bad_hour:02d}:xx"))

    # --- unusual_account_pair: swap one line to an implausible account -----
    all_account_keys = list(account_by_id["account_key"].values)
    for hid in take(ERROR_MIX["unusual_account_pair"]):
        hlines = lines_df[lines_df["header_id"] == hid]
        target_line = hlines.sample(1, random_state=int(hid)).iloc[0]
        current_key = target_line["account_key"]
        candidates = [k for k in all_account_keys if k != current_key]
        new_key = int(rng_local.choice(candidates))
        lines_df.loc[lines_df["line_id"] == target_line["line_id"], "account_key"] = new_key
        ground_truth.append(dict(header_id=hid, line_id=int(target_line["line_id"]),
                                  error_type="unusual_account_pair", detectability="hard",
                                  notes="Line reassigned to an account outside its normal pairing pool"))

    # --- structuring: replace one large entry with 2-4 smaller ones just
    # under a $10,000 approval threshold, spread over a few days, same
    # employee/account -----------------------------------------------------
    THRESHOLD = 10_000.0
    cash_line_hids = lines_df[lines_df["account_key"].isin(
        account_by_id[account_by_id["is_cash_account"]]["account_key"])]["header_id"].unique()
    # Restrict to simple 1-debit/1-credit originals. Splitting a payment
    # into pieces is itself a simple-payment pattern in practice, and it
    # keeps the per-piece amount assignment below unambiguous: with a
    # multi-line original, setting every debit line AND every credit line
    # to the same piece amount would silently break the balance (e.g. 2
    # debit lines + 1 credit line all set to `amt` sums debit=2*amt,
    # credit=amt) - an unintended imbalance that ground_truth wouldn't
    # label as 'unbalanced'. Restricting the pool avoids that case entirely.
    simple_hids = lines_df.groupby("header_id").size()
    simple_hids = simple_hids[simple_hids == 2].index.values
    struct_pool = np.intersect1d(np.intersect1d(eligible[cursor:], cash_line_hids), simple_hids)
    n_struct = int(round(ERROR_MIX["structuring"] * n_headers))
    struct_targets = struct_pool[:n_struct]
    cursor += n_struct

    for hid in struct_targets:
        orig_h = headers_df[headers_df["header_id"] == hid].iloc[0]
        orig_lines = lines_df[lines_df["header_id"] == hid].copy()
        total = float(orig_lines["debit_amount"].sum())
        if total < THRESHOLD:
            total = THRESHOLD * float(rng_local.uniform(1.5, 3.0))

        n_splits = int(rng_local.integers(2, 5))
        piece_target = total / n_splits
        # Each piece just under threshold, small random jitter so they don't
        # look identical (that would make it duplicate-detectable instead).
        pieces = [round(min(piece_target, THRESHOLD * 0.97) * float(rng_local.uniform(0.9, 0.99)), 2)
                  for _ in range(n_splits)]

        headers_df = headers_df[headers_df["header_id"] != hid]
        lines_df = lines_df[lines_df["header_id"] != hid]

        new_ids = []
        for j, amt in enumerate(pieces):
            gap = int(rng_local.integers(0, 4))
            new_h = orig_h.copy()
            new_h["header_id"] = next_header_id
            new_h["header_id_text"] = f"JE-{next_header_id:06d}"
            new_h["posting_datetime"] = orig_h["posting_datetime"] + timedelta(days=gap)
            new_h["entry_description"] = orig_h["entry_description"] + f" (split {j+1}/{n_splits})"
            headers_df = pd.concat([headers_df, pd.DataFrame([new_h])], ignore_index=True)

            for _, l in orig_lines.iterrows():
                nl = l.copy()
                nl["line_id"] = next_line_id
                nl["header_id"] = next_header_id
                side = "debit_amount" if l["debit_amount"] > 0 else "credit_amount"
                nl[side] = amt
                lines_df = pd.concat([lines_df, pd.DataFrame([nl])], ignore_index=True)
                next_line_id += 1
            new_ids.append(next_header_id)
            next_header_id += 1

        for nid in new_ids:
            ground_truth.append(dict(header_id=int(nid), line_id=None, error_type="structuring",
                                      detectability="hard",
                                      notes=f"1 of {n_splits} pieces split from original ~{total:.0f} total, "
                                            f"each kept under ${THRESHOLD:.0f} threshold"))

    headers_df = headers_df.sort_values("header_id").reset_index(drop=True)
    lines_df = lines_df.sort_values(["header_id", "line_num"]).reset_index(drop=True)
    # header_id already an int64 column (mixed int/None never entered it -
    # reversed_header_id is the analogous nullable-int case there). gt_df's
    # header_id/line_id mix real ids with None (header-level errors have no
    # line_id, and vice versa), which pandas upcasts to float64 - "239340.0"
    # is not valid bigint COPY text. pandas' nullable Int64 dtype keeps them
    # as integers and writes a clean empty field (-> \N) for the missing side.
    gt_df = pd.DataFrame(ground_truth).astype({"header_id": "Int64", "line_id": "Int64"})
    return headers_df, lines_df, gt_df


# ============================================================================
# Backdated error: transaction_date vs. posting_datetime.
#
# transaction_date models the standard accounting distinction between when a
# transaction actually happened and when it hit the GL. Every header gets a
# small baseline lag - that's normal processing delay, not an anomaly. A
# seeded subset instead gets its lag redrawn from a long-tailed lognormal:
# most of those still land in the same 0-2 day range as the baseline
# (indistinguishable from normal -> hard), a shrinking share land 3-9 days
# out (medium), and a tail reaches 10-180 days (easy). Overlapping the low
# end with the baseline is deliberate - a rule as simple as "lag > 0 ->
# anomaly" would violate CLAUDE.md's requirement that detectability vary.
# ============================================================================
def seed_backdated_errors(headers_df: pd.DataFrame, gt_df: pd.DataFrame, rng_local):
    n = len(headers_df)
    headers_df = headers_df.copy()

    baseline_lag = rng_local.poisson(0.4, size=n)  # mean 0.4d: mostly 0, some 1-2
    post_dates = pd.to_datetime(headers_df["posting_datetime"]).dt.normalize()
    headers_df["transaction_date"] = (post_dates - pd.to_timedelta(baseline_lag, unit="D")).dt.date

    # Eligible pool: not already carrying a ground_truth label, not a
    # reversal, not the original a reversal points back at - same
    # non-overlap discipline as seed_errors' `eligible` pool.
    already_flagged = set(gt_df["header_id"].dropna().astype(int))
    reversed_original_ids = set(headers_df["reversed_header_id"].dropna().astype(int))
    eligible = headers_df[
        (~headers_df["is_reversal"])
        & (~headers_df["header_id"].isin(reversed_original_ids))
        & (~headers_df["header_id"].isin(already_flagged))
    ]["header_id"].to_numpy(copy=True)  # pandas CoW can hand back a read-only view; shuffle needs write access
    rng_local.shuffle(eligible)

    n_backdated = int(round(BACKDATED_RATE * n))
    targets = eligible[:n_backdated]
    long_tail_lag = np.round(rng_local.lognormal(mean=0.35, sigma=1.3, size=len(targets))).astype(int)
    long_tail_lag = np.clip(long_tail_lag, 0, 180)

    override = pd.DataFrame({"header_id": targets, "lag": long_tail_lag})
    headers_df = headers_df.merge(override, on="header_id", how="left")
    mask = headers_df["lag"].notna()
    headers_df.loc[mask, "transaction_date"] = (
        pd.to_datetime(headers_df.loc[mask, "posting_datetime"]).dt.normalize()
        - pd.to_timedelta(headers_df.loc[mask, "lag"], unit="D")
    ).dt.date

    detail = override.merge(headers_df[["header_id", "posting_datetime"]], on="header_id")
    gt_rows = []
    for _, r in detail.iterrows():
        lag = int(r["lag"])
        detectability = "easy" if lag >= 10 else ("medium" if lag >= 3 else "hard")
        gt_rows.append(dict(
            header_id=int(r["header_id"]), line_id=None, bank_txn_id=None,
            error_type="backdated", detectability=detectability,
            notes=f"Transaction dated {lag}d before posting (posted {r['posting_datetime']:%Y-%m-%d})",
        ))

    headers_df = headers_df.drop(columns=["lag"]).sort_values("header_id").reset_index(drop=True)
    backdated_gt = pd.DataFrame(gt_rows).astype(
        {"header_id": "Int64", "line_id": "Int64", "bank_txn_id": "Int64"}
    )
    return headers_df, backdated_gt


# ============================================================================
# Bank feed: mirrors ledger cash movements, plus seeded unmatched_bank errors.
# ============================================================================
def generate_bank_transactions(headers_df: pd.DataFrame, lines_df: pd.DataFrame,
                                accounts: pd.DataFrame, rng_local) -> pd.DataFrame:
    """One bank_transactions row per journal_line that hits a cash account.
    Kept as plain columns with no ledger FK (see schema comment) - the
    _ledger_header_id/_ledger_line_id columns here are internal bookkeeping
    only, used by seed_unmatched_bank_errors to label ground truth, and are
    dropped before the table is loaded to Postgres."""
    cash_keys = set(accounts.loc[accounts["is_cash_account"], "account_key"])
    payroll_key = int(accounts.loc[accounts["account_id"] == "1010", "account_key"].iloc[0])

    cash_lines = lines_df[lines_df["account_key"].isin(cash_keys)].merge(
        headers_df[["header_id", "header_id_text", "posting_datetime"]], on="header_id"
    )

    rows = []
    bank_txn_id = 1
    for _, l in cash_lines.iterrows():
        if l["debit_amount"] > 0:
            amount = float(l["debit_amount"])
            counterparty = str(rng_local.choice(CUSTOMER_NAMES))
            label = "CUSTOMER RECEIPT"
        else:
            amount = -float(l["credit_amount"])
            if l["account_key"] == payroll_key:
                counterparty = PAYROLL_PROVIDER
                label = "PAYROLL TRANSFER"
            else:
                counterparty = str(rng_local.choice(VENDOR_NAMES))
                label = "VENDOR PAYMENT"

        clean_ref = f"{label} {l['header_id_text']} {counterparty}"
        # Clearing lag: bank posts 0-3 days after the ledger entry.
        value_date = (l["posting_datetime"] + timedelta(days=int(rng_local.integers(0, 4)))).date()

        rows.append({
            "bank_txn_id": bank_txn_id, "value_date": value_date, "amount": round(amount, 2),
            "reference": messify_reference(clean_ref, rng_local), "counterparty": counterparty,
            "_ledger_header_id": int(l["header_id"]), "_ledger_line_id": int(l["line_id"]),
        })
        bank_txn_id += 1

    return pd.DataFrame(rows)


def seed_unmatched_bank_errors(bank_df: pd.DataFrame, rng_local):
    """Seeds unmatched_bank in both directions: ledger-side orphans (drop the
    bank mirror of a real cash line - payment stuck in a suspense account,
    feed drop) and bank-side orphans (phantom rows - bank fees, interest,
    a payment the GL never recorded). detectability scales with dollar size:
    a large unmatched item is easy to spot on a bank rec; a small one is
    easy to miss, which is exactly the mixed-difficulty CLAUDE.md asks for."""
    n_cash_lines = len(bank_df)
    gt_rows = []

    n_drop = int(round(UNMATCHED_LEDGER_RATE * n_cash_lines))
    drop_idx = rng_local.choice(bank_df.index.values, size=n_drop, replace=False)
    dropped = bank_df.loc[drop_idx]
    bank_df = bank_df.drop(index=drop_idx).reset_index(drop=True)

    if len(dropped):
        terciles = dropped["amount"].abs().quantile([1 / 3, 2 / 3]).values
        for _, r in dropped.iterrows():
            a = abs(r["amount"])
            detectability = "easy" if a >= terciles[1] else ("medium" if a >= terciles[0] else "hard")
            gt_rows.append(dict(
                header_id=int(r["_ledger_header_id"]), line_id=int(r["_ledger_line_id"]), bank_txn_id=None,
                error_type="unmatched_bank", detectability=detectability,
                notes=f"Ledger cash line ${a:,.2f} has no bank feed counterpart",
            ))

    # Phantom bank rows: no ledger line behind them at all.
    n_phantom = int(round(UNMATCHED_PHANTOM_RATE * n_cash_lines))
    span_start = pd.to_datetime(bank_df["value_date"]).min()
    span_end = pd.to_datetime(bank_df["value_date"]).max()
    span_days = max((span_end - span_start).days, 1)
    next_id = int(bank_df["bank_txn_id"].max()) + 1 if len(bank_df) else 1

    phantom_rows = []
    for _ in range(n_phantom):
        is_fee = rng_local.random() < 0.5
        if is_fee:
            amount = -round(float(rng_local.uniform(5, 250)), 2)
            counterparty = "Bank Fees & Charges"
            clean_ref = f"BANK CHARGE {counterparty}"
        else:
            amount = round(float(rng_local.uniform(50, 5000)), 2)
            counterparty = str(rng_local.choice(VENDOR_NAMES + CUSTOMER_NAMES))
            clean_ref = f"MISC TRANSFER {counterparty}"
        value_date = (span_start + timedelta(days=int(rng_local.integers(0, span_days + 1)))).date()

        phantom_rows.append({
            "bank_txn_id": next_id, "value_date": value_date, "amount": amount,
            "reference": messify_reference(clean_ref, rng_local), "counterparty": counterparty,
            "_ledger_header_id": None, "_ledger_line_id": None,
        })
        detectability = "easy" if abs(amount) >= 1000 else ("medium" if abs(amount) >= 100 else "hard")
        gt_rows.append(dict(
            header_id=None, line_id=None, bank_txn_id=next_id,
            error_type="unmatched_bank", detectability=detectability,
            notes=f"Bank feed row ${abs(amount):,.2f} has no ledger counterpart",
        ))
        next_id += 1

    bank_df = pd.concat([bank_df, pd.DataFrame(phantom_rows)], ignore_index=True)
    bank_df = bank_df.sort_values("value_date").reset_index(drop=True)

    unmatched_gt = pd.DataFrame(gt_rows).astype(
        {"header_id": "Int64", "line_id": "Int64", "bank_txn_id": "Int64"}
    )
    return bank_df, unmatched_gt


# ============================================================================
# Validation
# ============================================================================
def validate(headers_df, lines_df, gt_df, bank_df):
    print(f"headers: {len(headers_df):,}")
    print(f"lines:   {len(lines_df):,}  (target ~{TARGET_LINES:,})")
    print(f"ground_truth rows: {len(gt_df):,} "
          f"({len(gt_df) / len(headers_df):.2%} of headers)")
    print("\nerror_type x detectability:")
    print(gt_df.groupby(["error_type", "detectability"]).size().unstack(fill_value=0))

    bal = lines_df.groupby("header_id").apply(
        lambda g: round(g["debit_amount"].sum() - g["credit_amount"].sum(), 2), include_groups=False
    )
    n_unbalanced = (bal != 0).sum()
    print(f"\nheaders where debit != credit: {n_unbalanced} "
          f"(expect ~= seeded 'unbalanced' count: {(gt_df['error_type'] == 'unbalanced').sum()})")

    amt = lines_df.loc[lines_df["debit_amount"] > 0, "debit_amount"]
    print(f"\namount distribution (debit lines): median={amt.median():.2f}, "
          f"mean={amt.mean():.2f}, p95={amt.quantile(.95):.2f}, p99={amt.quantile(.99):.2f}, max={amt.max():.2f}")
    print("(mean >> median confirms right-skew)")

    hours = pd.to_datetime(headers_df["posting_datetime"]).dt.hour
    business = hours.between(8, 18).mean()
    print(f"\nshare of postings within 08:00-18:00: {business:.1%}")

    lag_days = (pd.to_datetime(headers_df["posting_datetime"]).dt.normalize()
                - pd.to_datetime(headers_df["transaction_date"])).dt.days
    print(f"\ntransaction_date lag (posting - transaction), days: "
          f"median={lag_days.median():.1f}, mean={lag_days.mean():.2f}, "
          f"p95={lag_days.quantile(.95):.1f}, p99={lag_days.quantile(.99):.1f}, max={lag_days.max()}")
    print(f"seeded 'backdated' count: {(gt_df['error_type'] == 'backdated').sum()}")

    print(f"\nbank_transactions: {len(bank_df):,}")
    unmatched = gt_df[gt_df["error_type"] == "unmatched_bank"]
    n_ledger_orphan = unmatched["bank_txn_id"].isna().sum()
    n_bank_orphan = unmatched["header_id"].isna().sum()
    print(f"unmatched_bank ground truth: {n_ledger_orphan} ledger-side orphans "
          f"(no bank line), {n_bank_orphan} bank-side orphans (phantom rows)")


# ============================================================================
# Postgres load
# ============================================================================
def copy_df(cur, df: pd.DataFrame, table: str, columns: list):
    buf = io.StringIO()
    df[columns].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cur.copy_expert(
        f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
        buf,
    )


def load_to_postgres(dim_date, accounts, employees, headers_df, lines_df, gt_df, bank_df):
    if psycopg2 is None:
        print("psycopg2 not installed in this interpreter - cannot load to Postgres.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect()  # reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD
    try:
        with conn:
            with conn.cursor() as cur:
                copy_df(cur, accounts, "dim_account",
                        ["account_key", "account_id", "account_name", "account_type",
                         "normal_balance", "is_cash_account"])
                copy_df(cur, employees, "dim_employee",
                        ["employee_key", "employee_id", "employee_name", "department", "role",
                         "seniority_level", "typical_start_hour", "typical_end_hour"])
                copy_df(cur, dim_date, "dim_date",
                        ["date_key", "calendar_date", "year", "month", "day", "day_of_week",
                         "day_name", "is_weekend", "is_month_end", "fiscal_period"])
                copy_df(cur, headers_df, "journal_header",
                        ["header_id", "header_id_text", "posting_datetime", "transaction_date",
                         "date_key", "employee_key", "source_system", "entry_description",
                         "is_reversal", "reversed_header_id", "reversal_flag"])
                copy_df(cur, lines_df, "journal_line",
                        ["line_id", "header_id", "line_num", "account_key",
                         "debit_amount", "credit_amount", "line_description"])
                copy_df(cur, bank_df, "bank_transactions",
                        ["bank_txn_id", "value_date", "amount", "reference", "counterparty"])
                copy_df(cur, gt_df, "ground_truth",
                        ["header_id", "line_id", "bank_txn_id", "error_type", "detectability", "notes"])

                # Reset identity sequences so future manual inserts don't
                # collide with the explicit IDs COPY just loaded.
                for table, id_col, next_val in [
                    ("journal_header", "header_id", int(headers_df["header_id"].max()) + 1),
                    ("journal_line", "line_id", int(lines_df["line_id"].max()) + 1),
                    ("bank_transactions", "bank_txn_id", int(bank_df["bank_txn_id"].max()) + 1),
                ]:
                    cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{id_col}'), %s, false)",
                                (next_val,))
        print("Load committed.")
    finally:
        conn.close()


# ============================================================================
# Main
# ============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="generate and validate only, skip Postgres load")
    args = parser.parse_args()

    print("Building dimensions...")
    dim_date = build_dim_date(START_DATE, NUM_MONTHS)
    accounts = build_dim_account()
    employees = build_dim_employee()

    print("Generating clean journal population...")
    headers_df, lines_df, next_header_id, next_line_id, account_by_id, employees = \
        generate_clean_data(dim_date, accounts, employees)

    print("Seeding ground-truth errors...")
    headers_df, lines_df, gt_df = seed_errors(
        headers_df, lines_df, next_header_id, next_line_id, account_by_id, employees
    )
    gt_df["bank_txn_id"] = pd.array([pd.NA] * len(gt_df), dtype="Int64")

    print("Seeding backdated errors (transaction_date vs. posting_datetime)...")
    headers_df, backdated_gt = seed_backdated_errors(headers_df, gt_df, rng)

    print("Generating bank feed and seeding unmatched_bank errors...")
    bank_df = generate_bank_transactions(headers_df, lines_df, accounts, rng)
    bank_df, unmatched_gt = seed_unmatched_bank_errors(bank_df, rng)

    gt_df = pd.concat([gt_df, backdated_gt, unmatched_gt], ignore_index=True)

    print()
    validate(headers_df, lines_df, gt_df, bank_df)

    if args.dry_run:
        print("\n--dry-run: skipping Postgres load.")
        return

    print("\nLoading to Postgres...")
    load_to_postgres(dim_date, accounts, employees, headers_df, lines_df, gt_df, bank_df)


if __name__ == "__main__":
    main()
