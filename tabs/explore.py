from __future__ import annotations
import pandas as pd
import streamlit as st
from core import pd_of

try:
    import plotly.graph_objects as go
    _has_plotly = True
except ImportError:
    _has_plotly = False


def render_explore(df: pd.DataFrame, PD: list, grade_bands: list, seg_col: str) -> None:
    st.subheader("Score Distribution & Bad Rate")

    if not _has_plotly:
        st.info("Install plotly to enable this chart: `pip install plotly`")

    if _has_plotly and "score" in df.columns:
        _c1, _c2 = st.columns([1, 3])
        _bin_w = _c1.slider("Bin width", 2, 30, 6, key="score_bin_w")

        _smin = int(df["score"].min())
        _smax = int(df["score"].max())
        _edges = list(range(_smin, _smax + _bin_w + 1, _bin_w))
        _labels = [str(e) for e in _edges[:-1]]

        _tmp = df.copy()
        _tmp["_bin"] = pd.cut(
            _tmp["score"], bins=_edges, labels=_labels,
            right=False, include_lowest=True,
        )
        _grp = _tmp.groupby("_bin", observed=True)
        _cnt = _grp.size().reindex(_labels, fill_value=0)
        _br = (
            _grp["grade"]
            .apply(lambda gs: gs.map(lambda g: pd_of(g, PD)).mean() * 100)
            .reindex(_labels, fill_value=0)
        )

        _tick_step = max(1, len(_labels) // 20)
        _tick_vals = _labels[::_tick_step]

        _sfig = go.Figure()
        _sfig.add_trace(go.Bar(
            x=_labels, y=_cnt.values,
            name="# merchants",
            marker_color="rgba(226,109,46,0.75)",
            marker_line_width=0,
        ))
        _sfig.add_trace(go.Scatter(
            x=_labels, y=_br.values,
            name="Expected bad rate (%)",
            line=dict(color="#1b4fa3", width=1.5),
            yaxis="y2", mode="lines",
        ))
        _sfig.update_layout(
            height=320, margin=dict(t=10, b=50, l=50, r=70),
            xaxis=dict(
                title="Score bin", tickangle=-45,
                tickmode="array", tickvals=_tick_vals, ticktext=_tick_vals,
            ),
            yaxis=dict(title="# merchants", showgrid=True, gridcolor="#ececec"),
            yaxis2=dict(
                title="Bad rate (%)", overlaying="y", side="right",
                tickformat=".1f", ticksuffix="%",
                range=[0, max(float(_br.max()) * 1.2, 1)],
            ),
            legend=dict(orientation="h", yanchor="top", y=-0.3),
            bargap=0.05, plot_bgcolor="#ffffff",
            clickmode="event+select",
        )

        _sev = st.plotly_chart(
            _sfig, use_container_width=True,
            on_select="rerun", key="score_dist_chart",
        )

        _sel_bin = None
        try:
            _pts = (_sev.selection or {}).get("points", [])
            if _pts:
                _sel_bin = str(_pts[0].get("x", ""))
        except Exception:
            pass

        if _sel_bin:
            _lo = int(_sel_bin)
            _hi = _lo + _bin_w
            _drill = df[(df["score"] >= _lo) & (df["score"] < _hi)].copy()
            st.markdown(
                f"**Drill-down — score bin [{_lo}, {_hi})** · "
                f"{len(_drill):,} applicants"
            )
            _m1, _m2, _m3, _m4 = st.columns(4)
            _m1.metric("Count", f"{len(_drill):,}")
            _m2.metric("Avg grade", f"{_drill['grade'].mean():.2f}" if len(_drill) else "—")
            _m3.metric(
                "Exp bad rate",
                f"{_drill['grade'].map(lambda g: pd_of(g, PD)).mean():.1%}" if len(_drill) else "—",
            )
            _m4.metric("Segments", _drill["segment"].nunique() if "segment" in _drill else "—")

            _dd1, _dd2 = st.columns(2)
            with _dd1:
                st.caption(f"By {seg_col if seg_col != '(none — one group)' else 'segment'}")
                st.dataframe(
                    _drill.groupby("segment").size().rename("count").reset_index(),
                    use_container_width=True, hide_index=True,
                )
            with _dd2:
                st.caption("By grade")
                _gd = _drill.groupby("grade").agg(
                    count=("grade", "size"),
                    exp_bad_rate=("grade", lambda gs: f"{gs.map(lambda g: pd_of(g, PD)).mean():.1%}"),
                ).reset_index()
                st.dataframe(_gd, use_container_width=True, hide_index=True)

            with st.expander(f"Raw rows in [{_lo}, {_hi})", expanded=False):
                st.dataframe(_drill, use_container_width=True, height=260)
        else:
            st.caption("Click a bar to drill down into that score band.")

    st.divider()

    st.subheader("Data")
    gsearch = st.text_input("Search grid", "")
    show = df
    if gsearch:
        show = df[df.astype(str).apply(lambda r: r.str.contains(gsearch, case=False).any(), axis=1)]
    st.dataframe(show, use_container_width=True, height=260)
    st.download_button("Export filtered CSV", show.to_csv(index=False).encode(),
                       "filtered_export.csv", "text/csv")

    st.subheader("Pivot")
    cat_cols = [c for c in df.columns if df[c].nunique() <= 25]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    pc1, pc2, pc3 = st.columns(3)
    dims = pc1.multiselect("Group by", cat_cols, default=cat_cols[:1])
    measure = pc2.selectbox("Measure", ["count"] + num_cols)
    agg = pc3.selectbox("Aggregation", ["sum", "mean", "count"])
    if dims:
        if measure == "count" or agg == "count":
            piv = df.groupby(dims).size().reset_index(name="count").sort_values("count", ascending=False)
            val_col = "count"
        else:
            piv = (df.groupby(dims)[measure].agg(agg).reset_index()
                   .sort_values(measure, ascending=False))
            val_col = measure
        st.dataframe(piv, use_container_width=True, height=260)
        st.bar_chart(piv.set_index(piv[dims].astype(str).agg(" · ".join, axis=1))[val_col])
