import plotly.graph_objects as go
import streamlit as st

import metrics
from common import BLUE, data, method_picker, show

d = data()
st.title("Overview")
method = method_picker(d)

n = metrics.test_split_size(d)
st.caption(f"All figures are on the **6-month test split** (last 6 fiscal periods, {n:,} journal entries) - "
           f"not the full {metrics.FULL_LEDGER_HEADERS:,}-entry ledger. Models were trained on the earlier 18 periods.")

m = metrics.header_metrics(d, method)
cols = st.columns(5)
cols[0].metric("Recall", f"{m['recall']:.3f}")
cols[1].metric("Precision", f"{m['precision']:.3f}")
cols[2].metric("F1", f"{m['f1']:.3f}")
cols[3].metric("Entries flagged", f"{m['flags']:,}")
cols[4].metric("Analyst hours / month", f"{m['analyst_hours_per_month']:.1f}",
               help="False positives x 5 analyst-minutes, per month of the test period")

left, right = st.columns([3, 2])
with left:
    by_type = metrics.pair_recall(d, method).sort_values("recall")
    fig = go.Figure(go.Bar(
        x=by_type["recall"], y=by_type["error_type"], orientation="h", marker_color=BLUE,
        text=by_type["recall"].map("{:.3f}".format), textposition="outside",
        customdata=by_type[["caught", "n_pairs"]],
        hovertemplate="%{y}: %{x:.3f}<br>%{customdata[0]} of %{customdata[1]} pairs<extra></extra>"))
    fig.update_layout(title="Recall by error type")
    fig.update_xaxes(range=[0, 1.12], title=None)
    show(fig, 420)
    st.caption("Pair-level: an entry carrying two error types counts once for each.")
with right:
    by_tier = metrics.pair_recall(d, method, by="detectability").set_index("detectability")
    by_tier = by_tier.reindex(["hard", "medium", "easy"])
    fig = go.Figure(go.Bar(
        x=by_tier["recall"], y=by_tier.index, orientation="h", marker_color=BLUE,
        text=by_tier["recall"].map("{:.3f}".format), textposition="outside",
        hovertemplate="%{y}: %{x:.3f}<extra></extra>"))
    fig.update_layout(title="Recall by detectability tier")
    fig.update_xaxes(range=[0, 1.15], title=None)
    show(fig, 240)
    st.markdown(
        "**What to read here.** For the final system, seven of eight error types are caught at 0.79 or "
        "better. *Backdated* is the gap: 0.26 against a measured design ceiling of 0.84. The features that "
        "would close it (lag against each poster's own history, an interaction over the three risk drivers) "
        "are not in the feature table yet.\n\n"
        "Recall is half the story: every flag costs an analyst about five minutes, so read it next to the "
        "workload figure.")
