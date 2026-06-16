"""
Credit Decisioning Workbench — entry point
==========================================
Run:
    streamlit run app.py
"""
from __future__ import annotations
import pandas as pd
import streamlit as st

import os
from config import thb
from core import (
    aqi_reverse, grade_walk, aqi_limited_grade, score_optimal, seg_stats,
    resolve_pd, resolve_e31, resolve_econ, apply_column_mapping,
)
from data import make_sample_data
from ui import inject_theme, render_header, MARK_PATH
from sidebar import load_data, render_column_mapping, render_assumptions, render_global_filters

_DEFAULT_XLSX = os.path.join(os.path.dirname(__file__), "default_input.xlsx")
from tabs.explore    import render_explore
from tabs.cutoff     import render_cutoff
from tabs.economics  import render_economics
from tabs.aqi        import render_aqi
from tabs.analytics  import render_analytics
from tabs.simulator  import render_simulator
from tabs.risk       import render_risk


def _cutoffs_from_state(df: pd.DataFrame, segments: list, mode: str, grade_bands: list) -> dict:
    """Read the live per-segment cutoff sliders from session_state without rendering them.

    Mirrors the default logic in tabs/cutoff.py's sliders so every section sees the
    same cutoffs regardless of whether the Cutoff & KPIs section is the active one.
    """
    g_max = max(grade_bands) if grade_bands else 10
    cutoffs: dict = {}
    for seg in segments:
        key = f"cut_{seg}"
        if mode == "grade":
            default = min(6, g_max)
            val = st.session_state.get(key, default)
            cutoffs[seg] = min(max(int(val), 1), g_max)
        else:
            sdf = df[df["segment"] == seg]
            if len(sdf):
                lo, hi = int(sdf["score"].min()), int(sdf["score"].max())
            else:
                lo, hi = 0, 900
            val = st.session_state.get(key, 600)
            cutoffs[seg] = min(max(int(val), lo), hi)
    return cutoffs


def main():
    st.set_page_config(page_title="Credit Decisioning Workbench", layout="wide", page_icon=MARK_PATH)
    inject_theme()
    render_header(
        "Credit Decisioning Workbench",
        "Personal &amp; nano loan cutoff, economics &amp; asset quality — every panel respects the global filters",
    )

    # ── Data loading ─────────────────────────────────────────────────────────
    # df_default is always the baseline (default_input.xlsx or built-in sample).
    # df_raw is what the user uploaded, or df_default when nothing is uploaded.
    if os.path.exists(_DEFAULT_XLSX):
        df_default = pd.read_excel(_DEFAULT_XLSX, sheet_name="applicants", skiprows=2)
        df_default.columns = df_default.columns.str.lower()
    else:
        df_default = make_sample_data()

    df_raw = load_data(df_default)

    mapping = render_column_mapping(df_raw)
    score_col, grade_col = mapping["score_col"], mapping["grade_col"]
    seg_col, prod_col = mapping["seg_col"], mapping["prod_col"]
    early_segments = mapping["early_segments"]

    assumptions = render_assumptions(df_raw, score_col, early_segments)
    grade_bands, PD, E31, bands_df = (assumptions["grade_bands"], assumptions["PD"],
                                       assumptions["E31"], assumptions["bands_df"])
    ECON, PD_SEG, E31_SEG, ECON_SEG = (assumptions["ECON"], assumptions["PD_SEG"],
                                        assumptions["E31_SEG"], assumptions["ECON_SEG"])
    AQI = assumptions["AQI"]

    filters = render_global_filters(df_raw)
    mask, active = filters["mask"], filters["active"]

    df = df_raw[mask].copy()
    if not ECON:
        st.error("At least one product row is required in the Economics table.")
        st.stop()
    _default_prod = next(iter(ECON))

    df = apply_column_mapping(df, score_col, grade_col, seg_col, prod_col,
                              grade_bands, bands_df, _default_prod)

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
                PD_eff = resolve_pd(seg, PD, PD_SEG)
                E31_eff = resolve_e31(seg, E31, E31_SEG)
                ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
                if mode == "grade":
                    _, kstar = grade_walk(sdf, PD_eff, E31_eff, ECON_eff, grade_bands)
                    if opt_target.startswith("Profit ∧"):
                        alim = aqi_limited_grade(sdf, thr, E31_eff, grade_bands)
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
                    st.session_state[f"cut_{seg}"] = k
                else:
                    # score mode: sweep score thresholds directly per segment
                    aqi_thr = thr if opt_target.startswith("Profit ∧") else None
                    s_opt = score_optimal(sdf, PD_eff, E31_eff, ECON_eff, thr=aqi_thr)
                    # check if we actually found a profitable threshold
                    stt = seg_stats(sdf, s_opt, "score", PD_eff, E31_eff, ECON_eff)
                    if stt["pbt"] <= 0:
                        _no_profit_segs.append(seg)
                    elif aqi_thr is not None and stt["blended_e31"] > aqi_thr:
                        _no_aqi_segs.append(seg)
                    st.session_state[f"cut_{seg}"] = s_opt
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
    # st.tabs() loses the active tab on every rerun triggered by a widget inside
    # it (e.g. dragging a cutoff slider snaps back to the first tab). st.segmented_control
    # is a real stateful widget — its selection survives reruns from other widgets.
    SECTIONS = ["Explore", "Cutoff & KPIs", "Economics", "Asset Quality (AQI)",
                "Concentration & PSI", "Analytics", "Simulator"]
    section = st.segmented_control("Section", SECTIONS, default=SECTIONS[0], key="active_section")
    if not section:
        section = SECTIONS[0]

    # cutoffs must exist even when the Cutoff & KPIs tab isn't the active section,
    # since Economics/Analytics/Simulator below all read the live cutoff sliders.
    cutoffs = _cutoffs_from_state(df, segments, mode, grade_bands)

    if section == "Explore":
        render_explore(df, PD, grade_bands, seg_col, PD_SEG=PD_SEG)

    elif section == "Cutoff & KPIs":
        cutoffs = render_cutoff(df, segments, mode, opt_target, PD, E31, ECON, grade_bands, thr, thb,
                                PD_SEG=PD_SEG, E31_SEG=E31_SEG, ECON_SEG=ECON_SEG)

    elif section == "Economics":
        render_economics(df, cutoffs, mode, PD, ECON, grade_bands, segments, thb,
                         PD_SEG=PD_SEG, E31_SEG=E31_SEG, ECON_SEG=ECON_SEG)

    elif section == "Asset Quality (AQI)":
        render_aqi(df, AQI, E31, grade_bands, thr, E31_SEG=E31_SEG)

    elif section == "Concentration & PSI":
        render_risk(df, cutoffs, mode, thb, df_ref=df_default)

    elif section == "Analytics":
        render_analytics(df, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                         PD_SEG=PD_SEG, E31_SEG=E31_SEG, ECON_SEG=ECON_SEG)

    elif section == "Simulator":
        render_simulator(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                         df_ref=df_default, PD_SEG=PD_SEG, E31_SEG=E31_SEG, ECON_SEG=ECON_SEG)


if __name__ == "__main__":
    main()
