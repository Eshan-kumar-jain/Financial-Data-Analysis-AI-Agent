import plotly.graph_objects as go
import streamlit as st

import metrics
from common import BLUE, ORANGE, data, style

d = data()
op = metrics.operating_point(d)
lo, hi = metrics.BAND

st.title(f"Operating point - why XGBoost @ {op['threshold']:.2f}")
st.caption(f"Cost model: 5 analyst-minutes per false positive vs 96 expected minutes per miss "
           f"(480 x 20% escalation) = {op['cost_fn_fp_ratio']:.0f}:1. The cost surface is shallow, so the "
           f"threshold is an operating **band** ({lo:.2f}-{hi:.2f}), with {op['threshold']:.2f} its minimum.")

c = st.columns(4)
c[0].metric("Chosen threshold", f"{op['threshold']:.2f}", help=f"Band {lo:.2f}-{hi:.2f}")
c[1].metric("Recall @ threshold", f"{op['recall']:.3f}")
c[2].metric("Precision @ threshold", f"{op['precision']:.3f}")
c[3].metric("Analyst hours / month", f"{op['analyst_hours_per_month']:.1f}")

sw = metrics.sweep(d)


def band(fig):
    """Shade the operating band and mark the chosen threshold."""
    fig.add_vrect(x0=lo, x1=hi, fillcolor=BLUE, opacity=0.08, line_width=0,
                  annotation_text=f"band {lo:.2f}-{hi:.2f}", annotation_position="top left")
    fig.add_vline(x=op["threshold"], line_dash="dash", line_color="#52514e", line_width=1,
                  annotation_text=f"{op['threshold']:.2f}", annotation_position="top right")
    fig.update_xaxes(title="Threshold", range=[0, 1])
    return fig


left, right = st.columns([3, 2])
with left:
    fig = go.Figure()
    fig.add_scatter(x=sw["threshold"], y=sw["precision"], name="Precision", line=dict(color=BLUE, width=2))
    fig.add_scatter(x=sw["threshold"], y=sw["recall"], name="Recall", line=dict(color=ORANGE, width=2))
    fig.update_layout(title="Precision and recall across the threshold grid (XGBoost alone)",
                      hovermode="x unified")
    fig.update_yaxes(range=[0, 1.02])
    st.plotly_chart(style(band(fig), 470), width="stretch")
with right:
    fig = go.Figure(go.Scatter(x=sw["threshold"], y=sw["analyst_hours_per_month"], line=dict(color=BLUE, width=2),
                               hovertemplate="threshold %{x:.2f}: %{y:.1f} h/month<extra></extra>"))
    fig.update_layout(title="Review workload (analyst hours / month)")
    st.plotly_chart(style(band(fig), 225), width="stretch")
    fig = go.Figure(go.Scatter(x=sw["threshold"], y=sw["expected_cost_minutes"], line=dict(color=ORANGE, width=2),
                               hovertemplate="threshold %{x:.2f}: %{y:,.0f}<extra></extra>"))
    fig.update_layout(title=f"Expected cost (FP-equivalents) at {op['cost_fn_fp_ratio']:.0f}:1")
    st.plotly_chart(style(band(fig), 225), width="stretch")
