"""Sidebar UI — data upload, column mapping, assumption editors, and global filters.

Each function renders one sidebar section and returns the values app.py needs to
pass into the rest of the pipeline. Order matters: load_data() -> render_column_mapping()
-> render_assumptions() -> render_global_filters(), since each step's defaults depend
on the previous step's output (e.g. the Score→Grade bands need score_col first).
"""
from __future__ import annotations
import pandas as pd
import streamlit as st
from config import PD_DEFAULT, E31_DEFAULT, ECON_DEFAULT, AQI_DEFAULT, GRADE_BANDS


def load_data(df_default: pd.DataFrame) -> pd.DataFrame:
    """File uploader; returns the uploaded data, or df_default when nothing is uploaded."""
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
    return df_raw


def render_column_mapping(df_raw: pd.DataFrame) -> dict:
    """Returns score_col, grade_col, seg_col, prod_col, and the early (pre-filter) segment list."""
    st.sidebar.header("Column mapping")
    num_cols  = [c for c in df_raw.columns if pd.api.types.is_numeric_dtype(df_raw[c])]
    low_card  = [c for c in df_raw.columns
                 if not pd.api.types.is_numeric_dtype(df_raw[c])
                 and df_raw[c].nunique(dropna=True) <= 60]
    all_text  = [c for c in df_raw.columns
                 if not pd.api.types.is_numeric_dtype(df_raw[c])]

    sc_default   = next((i+1 for i, c in enumerate(num_cols) if c == "score"),   0)
    gr_default   = next((i+1 for i, c in enumerate(num_cols) if c == "grade"),   0)
    seg_default  = next((i+1 for i, c in enumerate(low_card) if c in ("segment", "seg", "segment_name")), 0)
    prod_default = next((i+1 for i, c in enumerate(all_text) if c in ("product", "prod", "product_type", "loan_type")), 0)

    score_col = st.sidebar.selectbox("Score column",
                                     ["(none)"] + num_cols,
                                     index=sc_default, key="score_col")
    grade_col = st.sidebar.selectbox("Grade column",
                                     ["(derive from score)"] + num_cols,
                                     index=gr_default, key="grade_col")
    seg_col   = st.sidebar.selectbox("Segment column",
                                     ["(none — one group)"] + low_card,
                                     index=seg_default, key="seg_col")
    prod_col  = st.sidebar.selectbox("Product column",
                                     ["(none — use default economics)"] + all_text,
                                     index=prod_default, key="prod_col")

    if seg_col != "(none — one group)" and seg_col in df_raw.columns:
        early_segments = sorted(df_raw[seg_col].astype(str).dropna().unique().tolist())
    else:
        early_segments = ["(all)"]

    return dict(score_col=score_col, grade_col=grade_col, seg_col=seg_col, prod_col=prod_col,
                early_segments=early_segments)


def render_assumptions(df_raw: pd.DataFrame, score_col: str, early_segments: list) -> dict:
    """Renders all assumption editors; returns grade_bands, PD, E31, bands_df, ECON,
    PD_SEG, E31_SEG, ECON_SEG, AQI.

    Persistence: after each editor, the derived values are written to plain
    session_state keys (_save_PD, _save_E31, …) so save_state() can serialise
    them without touching any widget key.  On restore, the same keys are used
    to seed the initial data passed to each editor.
    """
    st.sidebar.header("Assumptions")

    # ── Grade → PD ──────────────────────────────────────────────────────────
    with st.sidebar.expander("Grade → PD (%)", expanded=False):
        _s_pd  = st.session_state.get("_save_PD")
        _s_gb  = st.session_state.get("_save_grade_bands")
        if _s_pd is not None and _s_gb is not None and len(_s_pd) == len(_s_gb):
            _pd_init = pd.DataFrame({"grade": list(map(float, _s_gb)), "PD_%": list(map(float, _s_pd))})
        else:
            _pd_init = pd.DataFrame({"grade": list(map(float, GRADE_BANDS)), "PD_%": list(map(float, PD_DEFAULT))})
        pd_df = st.data_editor(
            _pd_init,
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
        st.session_state["_save_PD"] = PD
        st.session_state["_save_grade_bands"] = grade_bands

    # ── Grade → E31 ─────────────────────────────────────────────────────────
    with st.sidebar.expander("Grade → %Ever31@MOB3 (Path-3)", expanded=False):
        _s_e31 = st.session_state.get("_save_E31")
        e31_map = dict(zip(range(1, len(E31_DEFAULT) + 1), E31_DEFAULT))
        if _s_e31 is not None and len(_s_e31) == len(grade_bands):
            _e31_init = pd.DataFrame({
                "grade":    list(map(float, grade_bands)),
                "Ever31_%": list(map(float, _s_e31)),
            })
        else:
            _e31_init = pd.DataFrame({
                "grade":    list(map(float, grade_bands)),
                "Ever31_%": [float(e31_map.get(g, 0.0)) for g in grade_bands],
            })
        e31_df = st.data_editor(
            _e31_init,
            hide_index=True, num_rows="dynamic", key="e31_ed",
            column_config={
                "grade":    st.column_config.NumberColumn("Grade", min_value=1, step=1, format="%d"),
                "Ever31_%": st.column_config.NumberColumn("Ever31@MOB3 (%)", min_value=0.0,
                                                           step=0.001, format="%.4f"),
            },
        )
        e31_df = e31_df.dropna(subset=["grade", "Ever31_%"]).sort_values("grade").reset_index(drop=True)
        E31 = e31_df["Ever31_%"].tolist()
        st.session_state["_save_E31"] = E31

    # ── Score → Grade bands ──────────────────────────────────────────────────
    with st.sidebar.expander("Score → Grade bands", expanded=False):
        _s_bdf = st.session_state.get("_save_bands_df")
        if _s_bdf is not None:
            _bands_init = pd.DataFrame(_s_bdf)
        else:
            if score_col != "(none)" and score_col in df_raw.columns:
                sc = pd.to_numeric(df_raw[score_col], errors="coerce").dropna()
                smin_def = int(sc.min()) if len(sc) else 300
                smax_def = int(sc.max()) if len(sc) else 900
            elif "score" in df_raw.columns:
                smin_def = int(df_raw["score"].min())
                smax_def = int(df_raw["score"].max())
            else:
                smin_def, smax_def = 300, 900
            n = len(grade_bands)
            bw = (smax_def - smin_def) / n
            _bands_init = pd.DataFrame([{
                "grade":     g,
                "score_min": round(smax_def - g * bw),
                "score_max": round(smax_def - (g - 1) * bw) - (0 if g == 1 else 1),
            } for g in range(1, n + 1)])

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
        n = len(grade_bands)
        st.caption(f"Scores outside all bands → fallback grade {grade_bands[n // 2] if grade_bands else n // 2 + 1}")
        st.session_state["_save_bands_df"] = bands_df.to_dict("records")

    # ── Economics per product ────────────────────────────────────────────────
    with st.sidebar.expander("Economics per product", expanded=False):
        _s_econ = st.session_state.get("_save_ECON")
        if _s_econ is not None:
            _econ_rows = [dict(product=p, loan=float(v["loan"]), EIR_pct=v["eir"]*100,
                               COF_pct=v["cof"]*100, OPEX=float(v["opex"]), LGD_pct=v["lgd"]*100)
                          for p, v in _s_econ.items()]
        else:
            _econ_rows = [dict(product=p, loan=float(v["loan"]), EIR_pct=v["eir"]*100,
                               COF_pct=v["cof"]*100, OPEX=float(v["opex"]), LGD_pct=v["lgd"]*100)
                          for p, v in ECON_DEFAULT.items()]
        econ_df = st.data_editor(
            pd.DataFrame(_econ_rows), hide_index=True, num_rows="dynamic", key="econ_ed",
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
        st.session_state["_save_ECON"] = ECON

    # ── Segment overrides ────────────────────────────────────────────────────
    with st.sidebar.expander("Segment overrides (PD / E31 / Economics)", expanded=False):
        st.caption("Pick segments to give their own PD curve, E31 curve, or economics. "
                   "Segments left unselected fall back to the global defaults above.")

        pd_segs = st.multiselect("Segments with custom PD curve", early_segments, key="pd_seg_pick")
        PD_SEG: dict = {}
        _s_pd_seg = st.session_state.get("_save_PD_SEG", {})
        for seg in pd_segs:
            st.markdown(f"**PD curve — {seg}**")
            _saved_vals = _s_pd_seg.get(seg)
            if _saved_vals is not None and len(_saved_vals) == len(grade_bands):
                _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "PD_%": list(map(float, _saved_vals))})
            else:
                _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "PD_%": list(PD)})
            ed = st.data_editor(
                _seed, hide_index=True, num_rows="fixed", key=f"pdseg_{seg}",
                column_config={
                    "grade": st.column_config.NumberColumn("Grade", format="%d", disabled=True),
                    "PD_%":  st.column_config.NumberColumn("PD (%)", min_value=0.0, max_value=100.0,
                                                            step=0.01, format="%.2f"),
                },
            )
            PD_SEG[seg] = ed["PD_%"].tolist()
        st.session_state["_save_PD_SEG"] = PD_SEG

        st.divider()
        e31_segs = st.multiselect("Segments with custom Ever31@MOB3 curve", early_segments, key="e31_seg_pick")
        E31_SEG: dict = {}
        _s_e31_seg = st.session_state.get("_save_E31_SEG", {})
        for seg in e31_segs:
            st.markdown(f"**Ever31@MOB3 curve — {seg}**")
            _saved_vals = _s_e31_seg.get(seg)
            if _saved_vals is not None and len(_saved_vals) == len(grade_bands):
                _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "Ever31_%": list(map(float, _saved_vals))})
            else:
                _seed = pd.DataFrame({"grade": list(map(float, grade_bands)), "Ever31_%": list(E31)})
            ed = st.data_editor(
                _seed, hide_index=True, num_rows="fixed", key=f"e31seg_{seg}",
                column_config={
                    "grade":    st.column_config.NumberColumn("Grade", format="%d", disabled=True),
                    "Ever31_%": st.column_config.NumberColumn("Ever31@MOB3 (%)", min_value=0.0,
                                                               step=0.001, format="%.4f"),
                },
            )
            E31_SEG[seg] = ed["Ever31_%"].tolist()
        st.session_state["_save_E31_SEG"] = E31_SEG

        st.divider()
        econ_segs = st.multiselect("Segments with custom economics", early_segments, key="econ_seg_pick")
        ECON_SEG: dict = {}
        _s_econ_seg = st.session_state.get("_save_ECON_SEG", {})
        for seg in econ_segs:
            st.markdown(f"**Economics — {seg}**")
            _saved_econ = _s_econ_seg.get(seg)
            _src = _saved_econ if _saved_econ is not None else ECON
            _seed = pd.DataFrame([dict(product=p, loan=float(v["loan"]), EIR_pct=v["eir"]*100,
                                       COF_pct=v["cof"]*100, OPEX=float(v["opex"]), LGD_pct=v["lgd"]*100)
                                  for p, v in _src.items()])
            ed = st.data_editor(
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
            ed = ed.dropna(subset=["product"])
            ECON_SEG[seg] = {r["product"]: dict(loan=r["loan"], eir=r["EIR_pct"]/100, cof=r["COF_pct"]/100,
                                                opex=r["OPEX"], lgd=r["LGD_pct"]/100)
                             for _, r in ed.iterrows()}
        st.session_state["_save_ECON_SEG"] = ECON_SEG

    # ── AQI parameters ───────────────────────────────────────────────────────
    with st.sidebar.expander("AQI parameters", expanded=False):
        AQI = dict(
            cc=st.number_input("%Avg Credit Cost/yr", value=AQI_DEFAULT["cc"], step=0.01, format="%.2f", key="aqi_cc"),
            lgd=st.number_input("LGD %", value=float(AQI_DEFAULT["lgd"]), step=0.1, format="%.1f", key="aqi_lgd"),
            pd=st.number_input("PD roll 31→91 %", value=AQI_DEFAULT["pd"], step=0.1, format="%.1f", key="aqi_pd"),
            lc=st.number_input("Loss-curve factor", value=round(AQI_DEFAULT["lc"], 4), step=0.0001, format="%.4f", key="aqi_lc"),
        )

    return dict(grade_bands=grade_bands, PD=PD, E31=E31, bands_df=bands_df, ECON=ECON,
                PD_SEG=PD_SEG, E31_SEG=E31_SEG, ECON_SEG=ECON_SEG, AQI=AQI)


def render_global_filters(df_raw: pd.DataFrame) -> dict:
    """Auto-generated per-column filters; returns the boolean mask and active-filter labels."""
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

    return dict(mask=mask, active=active)
