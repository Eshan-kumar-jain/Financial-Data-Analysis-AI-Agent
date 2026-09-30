import streamlit as st

import metrics
from common import data, heatmap

d = data()
st.title("Method comparison - blind vs labelled")
st.caption("Two scoreboards, deliberately **not** merged into one ranking: blind methods never see a label, "
           "supervised models were trained on them. Each row is one method computed on its own.")

COLS = {"method": "Method", "precision": "Precision", "recall": "Recall", "f1": "F1",
        "flag_rate": "Flag rate", "analyst_hours_per_month": "Analyst hrs / month"}
FMT = {"Precision": "{:.3f}", "Recall": "{:.3f}", "F1": "{:.3f}", "Flag rate": "{:.1%}",
       "Analyst hrs / month": "{:.1f}"}


def board(letter):
    t = metrics.scoreboard(d, letter)[list(COLS)].rename(columns=COLS)
    st.dataframe(t.style.format(FMT), hide_index=True, width="stretch")


left, right = st.columns(2)
with left:
    st.subheader("Scoreboard A - unsupervised (realistic)")
    board("A")
    st.caption("No labels used. Best F1 0.27 (segmented IQR).")
with right:
    st.subheader("Scoreboard B - supervised (optimistic)")
    board("B")
    st.caption("Trained on labels. Logistic regression's recall is bought with an 18% flag rate.")

st.plotly_chart(heatmap(metrics.recall_matrix(d), "Recall by method and error type", zmax=1),
                width="stretch")
st.caption("Blind methods are amount detectors; models miss what they have no feature for "
           "(unbalanced, duplicate, unmatched_bank).")
