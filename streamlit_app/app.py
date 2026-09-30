"""Financial anomaly detection - evaluation dashboard (Streamlit).

Same five pages as the Power BI report (powerbi/anomaly_detection.pbip),
read from a Parquet snapshot of the eval_* tables - no database at runtime.

  streamlit run streamlit_app/app.py
"""
import streamlit as st

st.set_page_config(page_title="Anomaly detection - evaluation", layout="wide")

nav = st.navigation([
    st.Page("pages/overview.py", title="Overview", default=True),
    st.Page("pages/layered_detection.py", title="Layered detection"),
    st.Page("pages/method_comparison.py", title="Method comparison"),
    st.Page("pages/operating_point.py", title="Operating point"),
    st.Page("pages/workload.py", title="Workload"),
])

with st.sidebar:
    st.caption("Synthetic journal-entry data with seeded errors. Every figure is computed "
               "in metrics.py and validated against the project's SQL "
               "(scripts/validate_streamlit.py).")

nav.run()
