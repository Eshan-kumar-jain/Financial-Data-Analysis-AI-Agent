import plotly.graph_objects as go
import streamlit as st

import metrics
from common import BLUE, ORANGE, data, method_picker, show

d = data()
st.title("Review workload - what an analyst would face")
method = method_picker(d)
st.caption("Every flag is a review: true positives are seeded errors found, false positives are review time "
           "spent on clean entries.")

m = metrics.header_metrics(d, method)
c = st.columns(4)
c[0].metric("Entries flagged", f"{m['flags']:,}")
c[1].metric("Errors found", f"{m['tp']:,}")
c[2].metric("False alarms", f"{m['fp']:,}")
c[3].metric("Share of ledger flagged", f"{m['flag_rate']:.1%}")


def stacked(df, x, y, orientation, title, height):
    fig = go.Figure()
    for col, name, colour in (("tp", "Errors found", BLUE), ("fp", "False alarms", ORANGE)):
        fig.add_bar(**{x: df[col], y: df.iloc[:, 0]}, name=name, marker_color=colour, orientation=orientation,
                    hovertemplate=f"%{{{'y' if orientation == 'h' else 'x'}}}<br>{name}: "
                                  f"%{{{'x' if orientation == 'h' else 'y'}:,}}<extra></extra>")
    fig.update_layout(title=title, barmode="stack", bargap=0.3)
    fig.update_layout(height=height)
    return fig


left, right = st.columns([3, 2])
with left:
    per = metrics.workload(d, method, by="fiscal_period").sort_values("fiscal_period")
    show(stacked(per, "y", "x", "v", "Flags per fiscal period", 440))
with right:
    top = metrics.workload(d, method, by="poster").sort_values("flags", ascending=False).head(15)
    fig = stacked(top, "x", "y", "h", "Flags by poster (top 15)", 440)
    fig.update_yaxes(autorange="reversed")
    show(fig)
    st.caption("Top 15 of 39 posters make 89% of entries.")

st.info("Posters are pseudonymised (Poster 01-40) at export - no employee name is in this app's data. "
        "A production deployment would also restrict this view by role.", icon=":material/lock:")
