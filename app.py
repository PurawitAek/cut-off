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
from config import (
    PD_DEFAULT, E31_DEFAULT, ECON_DEFAULT, AQI_DEFAULT, GRADE_BANDS, thb,
)
from core import (
    aqi_reverse, grade_walk, aqi_limited_grade, score_optimal, seg_stats,
    resolve_pd, resolve_e31, resolve_econ,
)
from data import make_sample_data
from ui import inject_theme, render_header, MARK_PATH

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

    up = st.sidebar.file_uploader("Load applicant file", type=["csv", "xlsx", "xls"])
    if up is not None:
        if up.name.endswith(".csv"):
            df_raw = pd.read_csv(up)
        else:
            df_raw = pd.read_excel(up, sheet_name="applicants", skiprows=2)
        df_raw.columns = df_raw.columns.str.lower()
        st.sidebar.caption(f"Loaded **{up.name}** ({len(df_raw):,} rows). Default data used as baseline in Simulator.")
    else:
        df_raw = df_default
        st.sidebar.caption("Using **default_input.xlsx**. Upload a file to compare against it in Simulator.")
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

    if seg_col != "(none — one group)" and seg_col in df_raw.columns:
        _early_segments = sorted(df_raw[seg_col].astype(str).dropna().unique().tolist())
    else:
        _early_segments = ["(all)"]

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

    with st.sidebar.expander("Score → Grade bands", expanded=False):
        # default: equal-width bands derived from the score column range
        if score_col != "(none)" and score_col in df_raw.columns:
            _sc = pd.to_numeric(df_raw[score_col], errors="coerce").dropna()
            _smin_def = int(_sc.min()) if len(_sc) else 300
            _smax_def = int(_sc.max()) if len(_sc) else 900
        elif "score" in df_raw.columns:
            _smin_def = int(df_raw["score"].min())
            _smax_def = int(df_raw["score"].max())
        else:
            _smin_def, _smax_def = 300, 900

        _n = len(grade_bands)
        _bw = (_smax_def - _smin_def) / _n
        _bands_init = pd.DataFrame([{
            "grade":     g,
            "score_min": round(_smax_def - g * _bw),
            "score_max": round(_smax_def - (g - 1) * _bw) - (0 if g == 1 else 1),
        } for g in range(1, _n + 1)])

        bands_df = st.data_editor(
            _bands_init,
            hide_index=True, num_rows="dynamic", key="bands_ed",
            column_config={
                "grade":     st.column_config.NumberColumn("Grade", min_value=1, step=1, format="%d"),
                "score_min": st.column_config.NumberColumn("Score min", step=1, format="%d"),
                "score_max": st.column_config.NumberColumn("Score max", step=1, format="%d"),
            },
        )
        bands_df = (bands_df.dropna()
                    .astype({"grade": int, "score_min": int, "score_max": int})
                    .reset_index(drop=True))
        st.caption(f"Scores outside all bands → fallback grade {grade_bands[_n // 2] if grade_bands else _n // 2 + 1}")

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

    with st.sidebar.expander("Segment overrides (PD / E31 / Economics)", expanded=False):
        st.caption("Pick segments to give their own PD curve, E31 curve, or economics. "
                   "Segments left unselected fall back to the global defaults above.")

        _pd_segs = st.multiselect("Segments with custom PD curve", _early_segments, key="pd_seg_pick")
        PD_SEG: dict = {}
        for seg in _pd_segs:
            st.markdown(f"**PD curve — {seg}**")
            _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "PD_%": list(PD)})
            _ed = st.data_editor(
                _seed, hide_index=True, num_rows="fixed", key=f"pdseg_{seg}",
                column_config={
                    "grade": st.column_config.NumberColumn("Grade", format="%d", disabled=True),
                    "PD_%":  st.column_config.NumberColumn("PD (%)", min_value=0.0, max_value=100.0,
                                                            step=0.01, format="%.2f"),
                },
            )
            PD_SEG[seg] = _ed["PD_%"].tolist()

        st.divider()
        _e31_segs = st.multiselect("Segments with custom Ever31@MOB3 curve", _early_segments, key="e31_seg_pick")
        E31_SEG: dict = {}
        for seg in _e31_segs:
            st.markdown(f"**Ever31@MOB3 curve — {seg}**")
            _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "Ever31_%": list(E31)})
            _ed = st.data_editor(
                _seed, hide_index=True, num_rows="fixed", key=f"e31seg_{seg}",
                column_config={
                    "grade":    st.column_config.NumberColumn("Grade", format="%d", disabled=True),
                    "Ever31_%": st.column_config.NumberColumn("Ever31@MOB3 (%)", min_value=0.0,
                                                               step=0.001, format="%.4f"),
                },
            )
            E31_SEG[seg] = _ed["Ever31_%"].tolist()

        st.divider()
        _econ_segs = st.multiselect("Segments with custom economics", _early_segments, key="econ_seg_pick")
        ECON_SEG: dict = {}
        for seg in _econ_segs:
            st.markdown(f"**Economics — {seg}**")
            _seed = pd.DataFrame([dict(product=p, loan=float(v["loan"]), EIR_pct=v["eir"]*100,
                                       COF_pct=v["cof"]*100, OPEX=float(v["opex"]), LGD_pct=v["lgd"]*100)
                                  for p, v in ECON.items()])
            _ed = st.data_editor(
                _seed, hide_index=True, num_rows="dynamic", key=f"econseg_{seg}",
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
            _ed = _ed.dropna(subset=["product"])
            ECON_SEG[seg] = {r["product"]: dict(loan=r["loan"], eir=r["EIR_pct"]/100, cof=r["COF_pct"]/100,
                                                opex=r["OPEX"], lgd=r["LGD_pct"]/100)
                             for _, r in _ed.iterrows()}

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
        _fallback = grade_bands[len(grade_bands) // 2] if grade_bands else 5
        if len(bands_df) > 0:
            _grade_out = pd.Series(pd.NA, index=df.index, dtype="Int64")
            for _, _brow in bands_df.iterrows():
                _m = (df["score"] >= _brow["score_min"]) & (df["score"] <= _brow["score_max"])
                _grade_out[_m] = int(_brow["grade"])
            df["grade"] = _grade_out.fillna(_fallback).astype(int)
        else:
            df["grade"] = _fallback

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
