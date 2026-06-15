"""
Credit Decisioning Workbench — entry point
==========================================
Run:
    streamlit run app.py
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

from config import (
    PD_DEFAULT, E31_DEFAULT, ECON_DEFAULT, AQI_DEFAULT, GRADE_BANDS, thb,
)
from core import aqi_reverse, grade_walk, aqi_limited_grade
from data import make_sample_data
from tabs.explore   import render_explore
from tabs.cutoff    import render_cutoff
from tabs.economics import render_economics
from tabs.aqi       import render_aqi
from tabs.analytics import render_analytics


def main():
    st.set_page_config(page_title="Credit Decisioning Workbench", layout="wide")
    st.title("Credit Decisioning Workbench")
    st.caption("Personal & nano loan cutoff, economics & asset quality — every panel respects the global filters")

    # ── Data loading ─────────────────────────────────────────────────────────
    up = st.sidebar.file_uploader("Load applicant CSV", type="csv")
    if up is not None:
        df_raw = pd.read_csv(up)
        df_raw.columns = df_raw.columns.str.lower()
    else:
        df_raw = make_sample_data()
        st.sidebar.caption("Using built-in sample data (1000 rows). Upload a CSV to replace.")
    for col in ("score", "grade"):
        if col in df_raw.columns:
            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

    # ── Column mapping ───────────────────────────────────────────────────────
    st.sidebar.header("Column mapping")
    _num_cols  = [c for c in df_raw.columns if pd.api.types.is_numeric_dtype(df_raw[c])]
    _low_card  = [c for c in df_raw.columns
                  if not pd.api.types.is_numeric_dtype(df_raw[c])
                  and df_raw[c].nunique(dropna=True) <= 60]
    _all_text  = [c for c in df_raw.columns
                  if not pd.api.types.is_numeric_dtype(df_raw[c])]

    _sc_default   = next((i+1 for i, c in enumerate(_num_cols) if c == "score"),   0)
    _gr_default   = next((i+1 for i, c in enumerate(_num_cols) if c == "grade"),   0)
    _seg_default  = next((i+1 for i, c in enumerate(_low_card) if c in ("segment", "seg", "segment_name")), 0)
    _prod_default = next((i+1 for i, c in enumerate(_all_text) if c in ("product", "prod", "product_type", "loan_type")), 0)

    score_col = st.sidebar.selectbox("Score column",
                                     ["(none)"] + _num_cols,
                                     index=_sc_default, key="score_col")
    grade_col = st.sidebar.selectbox("Grade column",
                                     ["(derive from score)"] + _num_cols,
                                     index=_gr_default, key="grade_col")
    seg_col   = st.sidebar.selectbox("Segment column",
                                     ["(none — one group)"] + _low_card,
                                     index=_seg_default, key="seg_col")
    prod_col  = st.sidebar.selectbox("Product column",
                                     ["(none — use default economics)"] + _all_text,
                                     index=_prod_default, key="prod_col")

    # ── Assumptions ──────────────────────────────────────────────────────────
    st.sidebar.header("Assumptions")

    with st.sidebar.expander("Grade → PD (%)", expanded=False):
        pd_df = st.data_editor(
            pd.DataFrame({"grade": list(map(float, GRADE_BANDS)), "PD_%": list(map(float, PD_DEFAULT))}),
            hide_index=True, num_rows="dynamic", key="pd_ed",
            column_config={
                "grade": st.column_config.NumberColumn("Grade", min_value=1, step=1, format="%d"),
                "PD_%":  st.column_config.NumberColumn("PD (%)", min_value=0.0, max_value=100.0,
                                                        step=0.01, format="%.2f"),
            },
        )
        pd_df = pd_df.dropna(subset=["grade", "PD_%"]).sort_values("grade").reset_index(drop=True)
        grade_bands = pd_df["grade"].astype(int).tolist()
        PD = pd_df["PD_%"].tolist()

    with st.sidebar.expander("Grade → %Ever31@MOB3 (Path-3)", expanded=False):
        _e31_map = dict(zip(range(1, len(E31_DEFAULT) + 1), E31_DEFAULT))
        e31_init = pd.DataFrame({
            "grade":    list(map(float, grade_bands)),
            "Ever31_%": [float(_e31_map.get(g, 0.0)) for g in grade_bands],
        })
        e31_df = st.data_editor(
            e31_init,
            hide_index=True, num_rows="dynamic", key="e31_ed",
            column_config={
                "grade":    st.column_config.NumberColumn("Grade", min_value=1, step=1, format="%d"),
                "Ever31_%": st.column_config.NumberColumn("Ever31@MOB3 (%)", min_value=0.0,
                                                           step=0.001, format="%.4f"),
            },
        )
        e31_df = e31_df.dropna(subset=["grade", "Ever31_%"]).sort_values("grade").reset_index(drop=True)
        E31 = e31_df["Ever31_%"].tolist()

    with st.sidebar.expander("Economics per product", expanded=False):
        econ_rows = [dict(product=p, loan=float(v["loan"]), EIR_pct=v["eir"]*100,
                          COF_pct=v["cof"]*100, OPEX=float(v["opex"]), LGD_pct=v["lgd"]*100)
                     for p, v in ECON_DEFAULT.items()]
        econ_df = st.data_editor(
            pd.DataFrame(econ_rows), hide_index=True, num_rows="dynamic", key="econ_ed",
            column_config={
                "product":  st.column_config.TextColumn("Product"),
                "loan":     st.column_config.NumberColumn("Avg Loan (THB)", min_value=0, step=1000, format="%d"),
                "EIR_pct":  st.column_config.NumberColumn("EIR (%)", min_value=0.0, step=0.01, format="%.2f"),
                "COF_pct":  st.column_config.NumberColumn("COF (%)", min_value=0.0, step=0.01, format="%.2f"),
                "OPEX":     st.column_config.NumberColumn("OPEX/CAC (THB)", min_value=0, step=100, format="%d"),
                "LGD_pct":  st.column_config.NumberColumn("LGD (%)", min_value=0.0, max_value=100.0,
                                                            step=0.1, format="%.1f"),
            },
        )
        econ_df = econ_df.dropna(subset=["product"])
        ECON = {r["product"]: dict(loan=r["loan"], eir=r["EIR_pct"]/100, cof=r["COF_pct"]/100,
                                   opex=r["OPEX"], lgd=r["LGD_pct"]/100)
                for _, r in econ_df.iterrows()}

    with st.sidebar.expander("AQI parameters", expanded=False):
        AQI = dict(
            cc=st.number_input("%Avg Credit Cost/yr", value=AQI_DEFAULT["cc"], step=0.01, format="%.2f"),
            lgd=st.number_input("LGD %", value=float(AQI_DEFAULT["lgd"]), step=0.1, format="%.1f"),
            pd=st.number_input("PD roll 31→91 %", value=AQI_DEFAULT["pd"], step=0.1, format="%.1f"),
            lc=st.number_input("Loss-curve factor", value=round(AQI_DEFAULT["lc"], 4), step=0.0001, format="%.4f"),
        )

    # ── Global filters ───────────────────────────────────────────────────────
    st.sidebar.header("Global filters")
    mask = pd.Series(True, index=df_raw.index)
    active = []
    for col in df_raw.columns:
        s = df_raw[col]
        nun = s.nunique(dropna=True)
        if pd.api.types.is_numeric_dtype(s) and nun > 25:
            lo, hi = float(s.min()), float(s.max())
            a, b = st.sidebar.slider(col, lo, hi, (lo, hi))
            if (a, b) != (lo, hi):
                mask &= s.between(a, b); active.append(f"{col}: {a:g}–{b:g}")
        elif nun <= 25:
            opts = sorted(s.dropna().unique().tolist())
            pick = st.sidebar.multiselect(col, opts, default=[])
            if pick:
                mask &= s.isin(pick); active.append(f"{col}: {', '.join(map(str, pick))}")
        else:
            q = st.sidebar.text_input(f"{col} contains")
            if q:
                mask &= s.astype(str).str.contains(q, case=False, na=False, regex=False)
                active.append(f'{col}: "{q}"')

    df = df_raw[mask].copy()
    if not ECON:
        st.error("At least one product row is required in the Economics table.")
        st.stop()
    _default_prod = next(iter(ECON))

    # ── Column derivation ────────────────────────────────────────────────────
    if score_col != "(none)" and score_col in df.columns:
        df["score"] = pd.to_numeric(df[score_col], errors="coerce").fillna(0)
    elif "score" not in df.columns:
        df["score"] = 0

    if grade_col != "(derive from score)" and grade_col in df.columns:
        df["grade"] = pd.to_numeric(df[grade_col], errors="coerce")
    elif "grade" not in df.columns:
        _n = len(grade_bands)
        _s = df["score"]
        if _s.nunique() > 1:
            df["grade"] = (pd.cut(_s, bins=_n, labels=range(_n, 0, -1))
                           .astype(float).fillna(_n).astype(int))
        else:
            df["grade"] = grade_bands[_n // 2] if grade_bands else 1

    if seg_col != "(none — one group)" and seg_col in df.columns:
        df["segment"] = df[seg_col].astype(str).fillna("(unknown)")
    elif "segment" not in df.columns:
        df["segment"] = "(all)"

    if prod_col != "(none — use default economics)" and prod_col in df.columns:
        df["product"] = df[prod_col].astype(str).fillna(_default_prod)
    elif "product" not in df.columns:
        df["product"] = _default_prod

    st.markdown("**Active filters:** " + (" · ".join(active) if active else
                "_none — showing all " + f"{len(df_raw):,} applicants_"))

    # ── Cutoff mode + Apply button ───────────────────────────────────────────
    segments = sorted(df["segment"].unique().tolist())
    thr = aqi_reverse(AQI)["mob3"]

    c1, c2, c3 = st.columns([1.3, 1.3, 1])
    mode = c1.radio("Cutoff mode", ["grade", "score"], horizontal=True,
                    help="grade: approve grades 1..k · score: approve score ≥ threshold")
    opt_target = c2.radio("Optimize", ["Profit k*", "Profit ∧ AQI"], horizontal=True)

    if c3.button("Apply optimal to all", use_container_width=True):
        if mode == "score" and score_col == "(none)":
            st.warning("Score mode requires a mapped score column. Switch to grade mode or map a score column first.")
        else:
            _no_profit_segs: list = []
            _no_aqi_segs: list = []
            for seg in segments:
                sdf = df[df["segment"] == seg]
                _, kstar = grade_walk(sdf, PD, E31, ECON, grade_bands)
                if opt_target.startswith("Profit ∧"):
                    alim = aqi_limited_grade(sdf, thr, E31, grade_bands)
                    k = min(kstar, alim)
                    if k == 0:
                        if kstar > 0:
                            _no_aqi_segs.append(seg)
                        else:
                            _no_profit_segs.append(seg)
                        k = 1
                else:
                    k = kstar
                    if k == 0:
                        _no_profit_segs.append(seg)
                        k = 1
                if mode == "grade":
                    st.session_state[f"cut_{seg}"] = k
                else:
                    appr = sdf[sdf["grade"] <= k]
                    if len(appr):
                        st.session_state[f"cut_{seg}"] = int(appr["score"].min())
                    else:
                        st.session_state[f"cut_{seg}"] = int(sdf["score"].max())
            if _no_profit_segs:
                st.warning(
                    f"No profitable grade found for: **{', '.join(_no_profit_segs)}**. "
                    "Check Economics — OPEX may exceed revenue at current loan size. "
                    "Cutoff set to reject-all for these segments."
                )
            if _no_aqi_segs:
                st.warning(
                    f"AQI constraint blocks all grades for: **{', '.join(_no_aqi_segs)}**. "
                    "Every grade's blended %Ever31@MOB3 exceeds the threshold. "
                    "Cutoff set to tightest grade (1) for these segments."
                )

    # ── Tabs ─────────────────────────────────────────────────────────────────
    t_explore, t_cut, t_econ, t_aqi, t_dash = st.tabs(
        ["Explore", "Cutoff & KPIs", "Economics", "Asset Quality (AQI)", "Analytics"]
    )

    with t_explore:
        render_explore(df, PD, grade_bands, seg_col)

    with t_cut:
        cutoffs = render_cutoff(df, segments, mode, opt_target, PD, E31, ECON, grade_bands, thr, thb)

    with t_econ:
        render_economics(df, cutoffs, mode, PD, ECON, segments, thb)

    with t_aqi:
        render_aqi(df, AQI, E31, grade_bands, thr)

    with t_dash:
        render_analytics(df, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb)


if __name__ == "__main__":
    main()
