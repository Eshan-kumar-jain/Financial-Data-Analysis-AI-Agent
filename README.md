# Financial anomaly detection on journal entries

Posting errors and anomalous transactions in a general ledger are rare, varied, and expensive to miss, and no single detection method catches them all. This project seeds eight kinds of error into ~114k synthetic journal entries and evaluates SQL rules, statistical and unsupervised methods, bank reconciliation and supervised models against that ground truth, in PostgreSQL, Python and Power BI.

## Run locally

The Streamlit dashboard reads a Parquet snapshot committed in `streamlit_app/data/`, so it needs no database:

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r streamlit_app/requirements.txt
streamlit run streamlit_app/app.py
```

To rebuild the snapshot from Postgres and check it (needs `.env`, see `.env.example`):

```bash
python scripts/export_parquet.py        # eval_* tables -> streamlit_app/data/*.parquet
python scripts/validate_streamlit.py    # every metric vs the project's SQL, expects 0 mismatches
```
