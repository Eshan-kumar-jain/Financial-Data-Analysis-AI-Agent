"""Shared app plumbing: cached data load, colours, the single-method picker.

No metric logic here - that all lives in metrics.py.
"""
import plotly.graph_objects as go
import streamlit as st

import metrics

# Validated categorical palette (slots 1-2): meaning, not decoration.
BLUE = "#2a78d6"     # errors found / primary series
ORANGE = "#eb6834"   # false alarms / second series
HEAT = [[0.0, "rgba(42,120,214,0.04)"], [1.0, BLUE]]   # one-hue sequential for heatmaps


@st.cache_data(show_spinner="Loading evaluation snapshot...")
def data():
    """Parquet snapshot (~2.7 MB), read once per session and cached - widget
    changes re-run the page script but not this."""
    return metrics.load()


def method_picker(d, label="Method", default=metrics.FINAL, family=None, key=None):
    """Single-select only: every metric is computed for exactly one method."""
    options = metrics.methods(d, family=family)
    return st.selectbox(label, options, index=options.index(default) if default in options else 0,
                        key=key, help="One method at a time - flags and scores are never added across methods.")


def style(fig, height=380):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=40, b=8),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(showgrid=False)
    return fig


def heatmap(matrix, title, fmt=".3f", zmax=None):
    fig = go.Figure(go.Heatmap(
        z=matrix.values, x=list(matrix.columns), y=list(matrix.index),
        colorscale=HEAT, zmin=0, zmax=zmax, text=matrix.values, texttemplate=f"%{{text:{fmt}}}",
        hovertemplate="%{y}<br>%{x}: %{z:" + fmt + "}<extra></extra>", showscale=False, xgap=2, ygap=2))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title=title)
    return style(fig, height=60 + 38 * len(matrix))
