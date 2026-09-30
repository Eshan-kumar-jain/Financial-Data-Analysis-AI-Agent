import plotly.graph_objects as go
import streamlit as st

import metrics
from common import BLUE, ORANGE, data, heatmap, show

d = data()
st.title("Layered detection - who catches what")
st.caption("The final system is L1 OR L2 OR L3 OR L4. **First-catch** credits the earliest layer in running "
           "order (cheap SQL checks first, the model last) and sums to the system total. **Unique** counts "
           "what only that layer catches - what removing it would cost.")

la = metrics.layer_attribution(d)
fin = metrics.header_metrics(d, metrics.FINAL)

left, right = st.columns([1, 1])
with left:
    fig = go.Figure()
    fig.add_bar(y=la["layer"], x=la["first_catch_flags"], name="First-catch flags", orientation="h",
                marker_color=ORANGE, hovertemplate="%{y}<br>first-catch flags: %{x:,}<extra></extra>")
    fig.add_bar(y=la["layer"], x=la["first_catch_tp"], name="First-catch true positives", orientation="h",
                marker_color=BLUE, hovertemplate="%{y}<br>first-catch TP: %{x:,}<extra></extra>")
    fig.update_layout(title=f"Flags credited to each layer (sum: {la['first_catch_flags'].sum():,} flags, "
                            f"{la['first_catch_tp'].sum():,} TP)", barmode="group", bargap=0.35)
    fig.update_yaxes(autorange="reversed")
    show(fig, 340)
with right:
    overlap = fin["flags"] - la["unique_flags"].sum()
    st.markdown(f"**What removing a layer would cost** - only {overlap} of {fin['flags']:,} flags are "
                "caught by more than one layer.")
    st.dataframe(
        la[["layer", "flags", "unique_flags", "unique_tp"]].rename(columns={
            "layer": "Layer", "flags": "Flags", "unique_flags": "Only this layer",
            "unique_tp": "Only this layer (TP)"}),
        hide_index=True, width="stretch")

lp = metrics.layer_pairs(d).pivot(index="layer", columns="error_type", values="first_catch_pairs")
lp = lp.reindex(la["layer"])
show(heatmap(lp, "Error pairs caught first, by layer and error type", fmt="d"))
st.caption("Each layer owns different error types: the model catches none of unbalanced, duplicate or "
           "unmatched_bank first.")
