"""
Credit Decisioning Workbench  —  Streamlit app
==============================================

Personal & nano loan cutoff, economics and asset-quality (AQI) workbench.
A Python/Streamlit port of the HTML tool. Every panel respects the global filters.

The cutoff can be set in TWO modes (per segment), combining both earlier tools:
    • Grade (k)  — approve grades 1..k          (drives the marginal/cumulative KPIs)
    • Score      — approve score >= threshold   (the original score-slider behaviour)

Run:
    pip install streamlit pandas numpy matplotlib
    streamlit run credit_workbench.py

The calculation functions (grade_walk, seg_stats, aqi_*, p&l) depend only on
pandas/numpy and are import-safe without streamlit, so they can be unit-tested or
reused in a notebook / pipeline.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Default assumptions  (REPLACE the placeholder curves with observed figures)
# ----------------------------------------------------------------------------
PD_DEFAULT  = [2.2407,
                4.1279,
                4.8720,
                6.8772,
                13.5296,
                15.3127,
                22.8240,
                14.6083,
                7.6302,
                7.9774]            # % per grade 1..10
E31_DEFAULT = [0.05, 0.10, 0.20, 0.40, 0.70, 1.05, 1.60, 2.40, 3.50, 5.00]  # %Ever31@MOB3
ECON_DEFAULT = {
    "Personal Loan": dict(loan=30000, eir=0.2203, cof=0.015, opex=3312, lgd=0.865),
    "Nano Loan":     dict(loan=10000,  eir=0.33, cof=0.04, opex=3000,  lgd=0.865),
}
AQI_DEFAULT = dict(cc=16.38, lgd=75.0, pd=86.5, lc=0.0328)  # lc ≈ 0.0196 (from worked example)

GRADE_BANDS = list(range(1, 11))


# ----------------------------------------------------------------------------
# Sample data generator  (reproduces the mock CSV: 1000 rows, aligned score↔grade)
# ----------------------------------------------------------------------------
def make_sample_data(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    segs = {
        "PL-Salaried":     ("Personal Loan", 0.26, 3.4, 1.8),
        "PL-SelfEmployed": ("Personal Loan", 0.18, 4.6, 2.1),
        "PL-NewToCredit":  ("Personal Loan", 0.12, 5.6, 2.2),
        "Nano-Existing":   ("Nano Loan",     0.16, 5.2, 2.0),
        "Nano-MicroBiz":   ("Nano Loan",     0.16, 6.4, 2.1),
        "Nano-Informal":   ("Nano Loan",     0.12, 7.3, 1.9),
    }
    names = list(segs)
    w = np.array([segs[s][1] for s in names]); w = w / w.sum()
    top, bottom = 880, 320
    band = (top - bottom) / 9
    rows = []
    for i in range(n):
        s = names[rng.choice(len(names), p=w)]
        prod, _, gm, gsd = segs[s]
        g = int(np.clip(round(rng.normal(gm, gsd)), 1, 10))
        center = top - (g - 1) * (top - bottom) / 9
        score = int(np.clip(center + rng.normal(0, 12), center - band / 2, center + band / 2))
        rows.append((f"CUST{100000 + i}", prod, s, score, g))
    df = pd.DataFrame(rows, columns=["customerID", "product", "segment", "score", "grade"])
    return df.sample(frac=1, random_state=1).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Core credit calculations  (pure — pandas/numpy only)
# ----------------------------------------------------------------------------
def pd_of(g, PD):
    idx = int(g) - 1
    return (PD[idx] / 100.0) if 0 <= idx < len(PD) else 0.0

def e31_of(g, E31):
    idx = int(g) - 1
    return E31[idx] if 0 <= idx < len(E31) else 0.0
def econ_of(prod, ECON):
    return ECON.get(prod, next(iter(ECON.values())))

def pbt_per_acct(prod, g, PD, ECON):
    e = econ_of(prod, ECON)
    return e["loan"] * e["eir"] - e["loan"] * e["cof"] - e["opex"] - e["loan"] * pd_of(g, PD) * e["lgd"]

def rev_per_acct(prod, ECON):
    e = econ_of(prod, ECON)
    return e["loan"] * e["eir"] - e["loan"] * e["cof"]


def grade_walk(df: pd.DataFrame, PD, E31, ECON, grade_bands=None) -> tuple[pd.DataFrame, int]:
    """Walk grades best→worst; return per-grade marginal+cumulative table and k*.

    k* = grade that maximises cumulative PBT (approve grades 1..k*).
    Using marginal-PBT sign alone is wrong when the PD curve is non-monotonic
    (e.g. grade 8 has lower PD than grade 7), because that approach finds the
    *last* grade with positive marginal PBT instead of the true cumPBT peak.
    """
    if grade_bands is None:
        grade_bands = GRADE_BANDS
    out = []
    cumN = cumBad = cumLimit = cumLimitBad = cumPBT = 0.0
    for g in grade_bands:
        gl = df[df["grade"] == g]
        n = len(gl)
        pdg = pd_of(g, PD)
        loans = gl["product"].map(lambda p: econ_of(p, ECON)["loan"])
        bad = n * pdg
        limit = loans.sum()
        limit_bad = (loans * pdg).sum()
        marg_pbt = gl["product"].map(lambda p: pbt_per_acct(p, g, PD, ECON)).sum()
        cumN += n; cumBad += bad; cumLimit += limit; cumLimitBad += limit_bad; cumPBT += marg_pbt
        out.append(dict(
            grade=g, n=n,
            marg_bad_acct=pdg, marg_bad_limit=(limit_bad / limit if limit else 0.0),
            marg_pbt=marg_pbt,
            cum_n=int(cumN), cum_bad=cumBad, cum_bad_acct=(cumBad / cumN if cumN else 0.0),
            cum_limit=cumLimit, cum_bad_limit=(cumLimitBad / cumLimit if cumLimit else 0.0),
            cum_pbt=cumPBT,
        ))
    df_out = pd.DataFrame(out)
    # k* = grade where cumulative PBT is highest (must beat 0 = approve-nobody baseline)
    kstar = 0
    if not df_out.empty and df_out["cum_pbt"].max() > 0:
        kstar = int(df_out.loc[df_out["cum_pbt"].idxmax(), "grade"])
    return df_out, kstar


def approved_mask(df: pd.DataFrame, cutoffs: dict, mode: str) -> pd.Series:
    """Boolean mask of approved rows given per-segment cutoffs and the cutoff mode."""
    if mode == "grade":
        return df.apply(lambda r: r["grade"] <= cutoffs.get(r["segment"], 10), axis=1)
    else:  # score
        return df.apply(lambda r: r["score"] >= cutoffs.get(r["segment"], 0), axis=1)


def seg_stats(df_seg: pd.DataFrame, cutoff, mode: str, PD, E31, ECON) -> dict:
    if mode == "grade":
        appr = df_seg[df_seg["grade"] <= cutoff]
    else:
        appr = df_seg[df_seg["score"] >= cutoff]
    n, a = len(df_seg), len(appr)
    if a == 0:
        return dict(n=n, a=0, rate=0, avg_g=0, e_bad=0, e_bad_rate=0,
                    e_loss=0, pbt=0, rev=0, blended_e31=0)
    e_bad = appr["grade"].map(lambda g: pd_of(g, PD)).sum()
    e_loss = appr.apply(lambda r: econ_of(r["product"], ECON)["loan"] * pd_of(r["grade"], PD)
                        * econ_of(r["product"], ECON)["lgd"], axis=1).sum()
    pbt = appr.apply(lambda r: pbt_per_acct(r["product"], r["grade"], PD, ECON), axis=1).sum()
    rev = appr["product"].map(lambda p: rev_per_acct(p, ECON)).sum()
    blended_e31 = appr["grade"].map(lambda g: e31_of(g, E31)).mean()
    return dict(n=n, a=a, rate=a / n, avg_g=appr["grade"].mean(), e_bad=e_bad,
                e_bad_rate=e_bad / a, e_loss=e_loss, pbt=pbt, rev=rev, blended_e31=blended_e31)


# ---- AQI ----
def aqi_reverse(AQI) -> dict:
    cum = AQI["cc"] * 3
    e91 = cum / (AQI["lgd"] / 100)
    e31 = e91 / (AQI["pd"] / 100)
    mob3 = e31 * AQI["lc"]
    return dict(cum=cum, e91=e91, e31=e31, mob3=mob3)

def aqi_forward(obs, AQI) -> float:
    e31 = obs / AQI["lc"]
    e91 = e31 * (AQI["pd"] / 100)
    cum = e91 * (AQI["lgd"] / 100)
    return cum / 3

def aqi_limited_grade(df: pd.DataFrame, thr: float, E31, grade_bands=None) -> int:
    if grade_bands is None:
        grade_bands = GRADE_BANDS
    counts = df["grade"].value_counts()
    capN = capE = 0.0
    lim = 0
    for g in grade_bands:
        c = int(counts.get(g, 0))
        capN += c; capE += c * e31_of(g, E31)
        blended = capE / capN if capN else 0.0
        if blended <= thr:
            lim = g
    return lim


# ============================================================================
# Streamlit UI  (streamlit imported lazily so the functions above stay testable)
# ============================================================================
def main():
    import streamlit as st
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    try:
        import plotly.graph_objects as go
        _has_plotly = True
    except ImportError:
        _has_plotly = False

    st.set_page_config(page_title="Credit Decisioning Workbench", layout="wide")

    TEAL, GOOD, MID, BAD, INK, MUTE = "#0E7C86", "#2F9E6E", "#E8A33D", "#D9483B", "#16202C", "#5C6B7A"
    def gcolor(g): return GOOD if g <= 3 else (MID if g <= 7 else BAD)
    thb = lambda v: "฿{:,.0f}".format(v)

    # ---------- data ----------
    st.title("Credit Decisioning Workbench")
    st.caption("Personal & nano loan cutoff, economics & asset quality — every panel respects the global filters")

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

    # ---------- column mapping ----------
    st.sidebar.header("Column mapping")
    _num_cols  = [c for c in df_raw.columns if pd.api.types.is_numeric_dtype(df_raw[c])]
    _low_card  = [c for c in df_raw.columns
                  if not pd.api.types.is_numeric_dtype(df_raw[c])
                  and df_raw[c].nunique(dropna=True) <= 60]
    _all_text  = [c for c in df_raw.columns
                  if not pd.api.types.is_numeric_dtype(df_raw[c])]

    _sc_default   = next((i+1 for i, c in enumerate(_num_cols)  if c == "score"),   0)
    _gr_default   = next((i+1 for i, c in enumerate(_num_cols)  if c == "grade"),   0)
    _seg_default  = next((i+1 for i, c in enumerate(_low_card)  if c in ("segment", "seg", "segment_name")), 0)
    _prod_default = next((i+1 for i, c in enumerate(_all_text)  if c in ("product", "prod", "product_type", "loan_type")), 0)

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

    # ---------- assumptions (sidebar) ----------
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
                                   opex=r["OPEX"], lgd=r["LGD_pct"]/100) for _, r in econ_df.iterrows()}

    with st.sidebar.expander("AQI parameters", expanded=False):
        AQI = dict(
            cc=st.number_input("%Avg Credit Cost/yr", value=AQI_DEFAULT["cc"], step=0.01, format="%.2f"),
            lgd=st.number_input("LGD %", value=float(AQI_DEFAULT["lgd"]), step=0.1, format="%.1f"),
            pd=st.number_input("PD roll 31→91 %", value=AQI_DEFAULT["pd"], step=0.1, format="%.1f"),
            lc=st.number_input("Loss-curve factor", value=round(AQI_DEFAULT["lc"], 4), step=0.0001, format="%.4f"),
        )

    # ---------- global filters (auto-detected) ----------
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
        else:  # high-cardinality text
            q = st.sidebar.text_input(f"{col} contains")
            if q:
                mask &= s.astype(str).str.contains(q, case=False, na=False); active.append(f'{col}: "{q}"')

    df = df_raw[mask].copy()
    _default_prod = next(iter(ECON))

    # score
    if score_col != "(none)" and score_col in df.columns:
        df["score"] = pd.to_numeric(df[score_col], errors="coerce").fillna(0)
    elif "score" not in df.columns:
        df["score"] = 0

    # grade — use mapped column, or derive from score when not present
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

    # segment
    if seg_col != "(none — one group)" and seg_col in df.columns:
        df["segment"] = df[seg_col].astype(str).fillna("(unknown)")
    elif "segment" not in df.columns:
        df["segment"] = "(all)"

    # product
    if prod_col != "(none — use default economics)" and prod_col in df.columns:
        df["product"] = df[prod_col].astype(str).fillna(_default_prod)
    elif "product" not in df.columns:
        df["product"] = _default_prod
    st.markdown("**Active filters:** " + (" · ".join(active) if active else "_none — showing all "
                + f"{len(df_raw):,} applicants_"))

    # ---------- cutoff mode + per-segment cutoffs ----------
    segments = sorted(df["segment"].unique().tolist())
    thr = aqi_reverse(AQI)["mob3"]

    c1, c2, c3 = st.columns([1.3, 1.3, 1])
    mode = c1.radio("Cutoff mode", ["grade", "score"], horizontal=True,
                    help="grade: approve grades 1..k · score: approve score ≥ threshold")
    opt_target = c2.radio("Optimize", ["Profit k*", "Profit ∧ AQI"], horizontal=True)

    # optimal k per segment (grade mode) used by the Apply button
    def optimal_k(seg_df):
        _, kstar = grade_walk(seg_df, PD, E31, ECON, grade_bands)
        if opt_target.startswith("Profit ∧"):
            return min(kstar, aqi_limited_grade(seg_df, thr, E31, grade_bands))
        return kstar

    if c3.button("Apply optimal to all", use_container_width=True):
        _no_profit_segs = []
        for seg in segments:
            sdf = df[df["segment"] == seg]
            if mode == "grade":
                k = optimal_k(sdf)
                if k == 0:
                    _no_profit_segs.append(seg)
                    k = 1  # grade slider min is 1; grade 1 = tightest possible cutoff
                st.session_state[f"cut_{seg}"] = k
            else:
                k = optimal_k(sdf)
                appr = sdf[sdf["grade"] <= k]
                if len(appr):
                    st.session_state[f"cut_{seg}"] = int(appr["score"].min())
                else:
                    # no profitable grade → set threshold to data max (approve nobody)
                    st.session_state[f"cut_{seg}"] = int(sdf["score"].max())
                    _no_profit_segs.append(seg)
        if _no_profit_segs:
            st.warning(
                f"No profitable grade found for: **{', '.join(_no_profit_segs)}**. "
                "Check Economics — OPEX may exceed revenue at current loan size. "
                "Cutoff set to reject-all for these segments."
            )

    # ---------- tabs ----------
    t_explore, t_cut, t_econ, t_aqi, t_dash = st.tabs(
        ["Explore", "Cutoff & KPIs", "Economics", "Asset Quality (AQI)", "Analytics"])

    # =========================== EXPLORE ===========================
    with t_explore:
        # ---- Score Distribution & Bad Rate chart ----
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

            # ---- Drill-down on bar/point click ----
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

    # =========================== CUTOFF ===========================
    with t_cut:
        # per-segment cutoff controls
        st.subheader("Per-segment cutoff")
        cutoffs = {}
        cols = st.columns(3)
        order = sorted(segments, key=lambda s: df[df["segment"] == s]["grade"].mean())
        for i, seg in enumerate(order):
            sdf = df[df["segment"] == seg]
            with cols[i % 3]:
                if mode == "grade":
                    g_max = max(grade_bands) if grade_bands else 10
                    _gr_key = f"cut_{seg}"
                    if _gr_key in st.session_state:
                        st.session_state[_gr_key] = min(max(int(st.session_state[_gr_key]), 1), g_max)
                    default = int(st.session_state.get(_gr_key, min(6, g_max)))
                    default = min(max(default, 1), g_max)
                    k = st.slider(f"{seg}", 1, g_max, default, key=_gr_key)
                    cutoffs[seg] = k
                    stt = seg_stats(sdf, k, "grade", PD, E31, ECON)
                    _, kstar = grade_walk(sdf, PD, E31, ECON, grade_bands)
                    alim = aqi_limited_grade(sdf, thr, E31, grade_bands)
                    rec = min(kstar, alim) if opt_target.startswith("Profit ∧") else kstar
                    st.caption(f"{sdf.iloc[0]['product']} · {len(sdf):,} · approve **{stt['rate']:.0%}** · "
                               f"bad {stt['e_bad_rate']:.1%} · PBT {thb(stt['pbt'])}  \n"
                               f"rec k={rec} (k*={kstar}, AQI≤{alim})")
                else:
                    lo, hi = int(sdf["score"].min()), int(sdf["score"].max())
                    _sc_key = f"cut_{seg}"
                    if _sc_key in st.session_state:
                        st.session_state[_sc_key] = min(max(int(st.session_state[_sc_key]), lo), hi)
                    default = int(st.session_state.get(_sc_key, 600))
                    default = min(max(default, lo), hi)
                    c = st.slider(f"{seg}", lo, hi, default, key=_sc_key)
                    cutoffs[seg] = c
                    stt = seg_stats(sdf, c, "score", PD, E31, ECON)
                    st.caption(f"{sdf.iloc[0]['product']} · {len(sdf):,} · approve **{stt['rate']:.0%}** · "
                               f"bad {stt['e_bad_rate']:.1%} · PBT {thb(stt['pbt'])}")

        # portfolio KPIs
        st.subheader("Portfolio KPIs")
        A = bad = loss = pbt = rev = gsum = e31w = 0.0
        for seg in segments:
            sdf = df[df["segment"] == seg]
            stt = seg_stats(sdf, cutoffs[seg], mode, PD, E31, ECON)
            A += stt["a"]; bad += stt["e_bad"]; loss += stt["e_loss"]; pbt += stt["pbt"]
            rev += stt["rev"]; gsum += stt["avg_g"] * stt["a"]; e31w += stt["blended_e31"] * stt["a"]
        N = len(df)
        avg_g = gsum / A if A else 0
        blended_e31 = e31w / A if A else 0
        headroom = thr - blended_e31
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Approval", f"{(A/N if N else 0):.1%}", f"{int(A):,} of {N:,}")
        k2.metric("Avg approve grade", f"{avg_g:.2f}")
        k3.metric("Expected bad", f"{(bad/A if A else 0):.1%}", f"{bad:,.0f} accts",
                  delta_color="inverse")
        k4.metric("Expected loss", thb(loss))
        k5.metric("Expected PBT", thb(pbt), f"{(pbt/rev if rev else 0):.1%} of rev")
        k6.metric("AQI headroom", f"{headroom*100:+.2f}pp", f"{blended_e31:.2f}% vs {thr:.2f}%",
                  delta_color="off")

        # grade walk table
        st.subheader("Grade walk — marginal & cumulative (filtered portfolio)")
        walk, kstar = grade_walk(df, PD, E31, ECON, grade_bands)
        disp = walk.copy()
        disp["marg_bad_acct"] = disp["marg_bad_acct"].map("{:.1%}".format)
        disp["marg_bad_limit"] = disp["marg_bad_limit"].map("{:.1%}".format)
        disp["cum_bad_acct"] = disp["cum_bad_acct"].map("{:.1%}".format)
        disp["cum_bad_limit"] = disp["cum_bad_limit"].map("{:.1%}".format)
        for c in ("marg_pbt", "cum_pbt", "cum_limit"):
            disp[c] = disp[c].map("{:,.0f}".format)
        disp["cum_bad"] = disp["cum_bad"].map("{:,.0f}".format)
        st.caption(f"k\\* (last grade with marginal PBT ≥ 0) = **{kstar}**")
        st.dataframe(disp, use_container_width=True, hide_index=True)

    # =========================== ECONOMICS ===========================
    with t_econ:
        st.subheader("P&L waterfall — approved population")
        scope = st.selectbox("Scope", ["Whole approved portfolio"] + segments)
        appr = df[approved_mask(df, cutoffs, mode)]
        if scope != "Whole approved portfolio":
            appr = appr[appr["segment"] == scope]
        NII = appr["product"].map(lambda p: econ_of(p, ECON)["loan"] * econ_of(p, ECON)["eir"]).sum()
        COF = appr["product"].map(lambda p: econ_of(p, ECON)["loan"] * econ_of(p, ECON)["cof"]).sum()
        OPEX = appr["product"].map(lambda p: econ_of(p, ECON)["opex"]).sum()
        CC = appr.apply(lambda r: econ_of(r["product"], ECON)["loan"] * pd_of(r["grade"], PD)
                        * econ_of(r["product"], ECON)["lgd"], axis=1).sum()
        REV = NII - COF; PBT = REV - OPEX - CC

        fig, ax = plt.subplots(figsize=(9, 3.2))
        labels = ["NII", "COF", "REV", "OPEX", "Credit Cost", "PBT"]
        deltas = [NII, -COF, None, -OPEX, -CC, None]   # None = total bar
        totals = [None, None, REV, None, None, PBT]
        run = 0
        for i, lab in enumerate(labels):
            if totals[i] is not None:
                ax.bar(i, totals[i], color=TEAL if totals[i] >= 0 else BAD, alpha=0.9)
                run = totals[i]
                ax.text(i, totals[i], f"{abs(totals[i]):,.0f}", ha="center",
                        va="bottom" if totals[i] >= 0 else "top", fontsize=8)
            else:
                d = deltas[i]
                ax.bar(i, d, bottom=run, color=GOOD if d >= 0 else BAD, alpha=0.8)
                ax.text(i, run + d, f"{abs(d):,.0f}", ha="center",
                        va="bottom" if d >= 0 else "top", fontsize=8)
                run += d
        ax.axhline(0, color=MUTE, lw=0.8)
        ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, fontsize=9)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1e6:.1f}M"))
        ax.spines[["top", "right"]].set_visible(False)
        st.pyplot(fig)

        n = max(len(appr), 1)
        wf = pd.DataFrame({
            "line": ["NII", "COF", "Revenue", "OPEX", "Credit Cost (EL)", "PBT", "Accounts"],
            "THB": [NII, -COF, REV, -OPEX, -CC, PBT, len(appr)],
            "per account": [NII/n, -COF/n, REV/n, -OPEX/n, -CC/n, PBT/n, np.nan],
        })
        st.dataframe(wf, use_container_width=True, hide_index=True)

    # =========================== AQI ===========================
    with t_aqi:
        r = aqi_reverse(AQI)
        st.subheader("Reverse — target → threshold")
        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("%Avg Credit Cost/yr", f"{AQI['cc']:.2f}%")
        a2.metric("×3 → Cum 3yr", f"{r['cum']:.2f}%")
        a3.metric("÷LGD → Ever91 3yr", f"{r['e91']:.2f}%")
        a4.metric("÷PD → Ever31 3yr", f"{r['e31']:.2f}%")
        a5.metric("×curve → Ever31@MOB3", f"{r['mob3']:.2f}%")
        ref = [42.00, 48.55, 56.13, 1.10]
        got = [r["cum"], r["e91"], r["e31"], r["mob3"]]
        at_default = abs(AQI["cc"]-14) < 1e-9 and abs(AQI["lgd"]-86.5) < 1e-9 and abs(AQI["pd"]-86.5) < 1e-9
        if at_default:
            ok = all(abs(a-b) < 0.05 for a, b in zip(got, ref))
            (st.success if ok else st.error)(
                f"{'✓' if ok else '✗'} unit test  14.00 → 42.00 → 48.55 → 56.13 → 1.10")
        else:
            st.info("Custom parameters — chain recomputed live")

        st.subheader("Forward — observed → implied")
        f1, f2, f3 = st.columns(3)
        obs = f1.number_input("Observed %Ever31@MOB3", value=1.10, step=0.05)
        implied = aqi_forward(obs, AQI)
        f2.metric("Implied Credit Cost/yr", f"{implied:.2f}%")
        over = implied > AQI["cc"]
        f3.metric("Vs breakeven", "above" if over else "within", f"target {AQI['cc']:.1f}%",
                  delta_color="inverse")

        st.subheader("AQI link to cutoff")
        counts = df["grade"].value_counts()
        capN = capE = 0.0; series = []; breach = None
        for g in grade_bands:
            c = int(counts.get(g, 0)); capN += c; capE += c * e31_of(g, E31)
            bl = capE / capN if capN else 0
            series.append((g, bl))
            if bl > thr and breach is None:
                breach = g
        fig2, ax2 = plt.subplots(figsize=(9, 2.6))
        xs = [s[0] for s in series]; ys = [s[1] for s in series]
        ax2.plot(xs, ys, "-o", color=TEAL)
        for g, bl in series:
            ax2.plot(g, bl, "o", color=(BAD if bl > thr else TEAL))
        ax2.axhline(thr, color=BAD, ls="--", lw=1.2)
        g_last = grade_bands[-1] if grade_bands else 10
        ax2.text(g_last, thr, f" AQI {thr:.2f}%", color=BAD, va="bottom", ha="right", fontsize=8)
        ax2.set_xlabel("approve grades 1..k"); ax2.set_xticks(grade_bands)
        ax2.set_ylabel("blended %Ever31@MOB3")
        ax2.spines[["top", "right"]].set_visible(False)
        st.pyplot(fig2)
        aqi_k = (breach - 1) if breach else g_last
        st.caption(f"Blended %Ever31@MOB3 stays under the {thr:.2f}% threshold through grade **{aqi_k}**"
                   + (f", then breaches at grade {breach}." if breach else " (never breaches).")
                   + " Recommended cutoff = the tighter of profit k\\* and this AQI-limited grade.")


    # =========================== ANALYTICS DASHBOARD ===========================
    with t_dash:
        # ── Shared controls ─────────────────────────────────────────────────
        dc1, dc2, dc3, dc4, dc5 = st.columns([1.4, 1.4, 1, 1, 1])
        with dc1:
            all_segs = sorted(df["segment"].unique().tolist())
            dash_segs = st.multiselect(
                "Segment", all_segs, default=all_segs, key="dash_seg_filter"
            )
        with dc2:
            all_prods = sorted(df["product"].unique().tolist())
            dash_prods = st.multiselect(
                "Product", all_prods, default=all_prods, key="dash_prod_filter"
            )
        with dc3:
            metric_view = st.radio(
                "View", ["Marginal", "Cumulative"], horizontal=True, key="dash_metric_view"
            )
        with dc4:
            bad_rate_axis = st.radio(
                "Bad rate by", ["Account", "Credit limit"], horizontal=True, key="dash_bad_axis"
            )
        with dc5:
            color_by = st.radio(
                "Color by", ["None", "Segment", "Product"], horizontal=True, key="dash_color_by"
            )

        if not dash_segs or not dash_prods:
            st.warning("Select at least one segment and one product.")
            st.stop()

        df_dash = df[df["segment"].isin(dash_segs) & df["product"].isin(dash_prods)].copy()
        if df_dash.empty:
            st.warning("No data for the selected filters.")
            st.stop()

        walk_dash, kstar_dash = grade_walk(df_dash, PD, E31, ECON, grade_bands)
        aqi_grade_dash = aqi_limited_grade(df_dash, thr, E31, grade_bands)

        # groups for multi-line charts
        _cb_col = color_by.lower()  # "segment" or "product"
        groups = (
            sorted(df_dash[_cb_col].unique().tolist())
            if color_by != "None" else None
        )

        # helper: build wide DataFrame (grade index, one column per group)
        def _wide(walk_col, grp_col=None, scale=1.0):
            if grp_col is None:
                return (walk_dash.set_index("grade")[[walk_col]] * scale)
            out = {}
            for grp in groups:
                sub = df_dash[df_dash[grp_col] == grp]
                w, _ = grade_walk(sub, PD, E31, ECON, grade_bands)
                out[grp] = (w.set_index("grade")[walk_col] * scale).values
            return pd.DataFrame(out, index=walk_dash["grade"].tolist())

        # ── Shared KPI pre-computation (aggregate) ──────────────────────────
        appr_mask_dash = approved_mask(df_dash, cutoffs, mode)
        df_appr_dash = df_dash[appr_mask_dash]
        N_dash, A_dash = len(df_dash), len(df_appr_dash)
        appr_rate = A_dash / N_dash if N_dash else 0.0
        avg_grade_appr = df_appr_dash["grade"].mean() if A_dash else 0.0
        e_bad_count = df_appr_dash["grade"].map(lambda g: pd_of(g, PD)).sum()
        e_bad_rate = e_bad_count / A_dash if A_dash else 0.0
        pbt_total = df_appr_dash.apply(
            lambda r: pbt_per_acct(r["product"], r["grade"], PD, ECON), axis=1
        ).sum()
        rev_total = df_appr_dash["product"].map(lambda p: rev_per_acct(p, ECON)).sum()
        pbt_pct = pbt_total / rev_total if rev_total else 0.0
        blended_e31_dash = (
            df_appr_dash["grade"].map(lambda g: e31_of(g, E31)).mean() if A_dash else 0.0
        )
        aqi_headroom = thr - blended_e31_dash
        eff_cutoff = (
            min(cutoffs.get(s, max(grade_bands)) for s in dash_segs)
            if dash_segs else max(grade_bands)
        )
        count_col = "cum_n" if metric_view == "Cumulative" else "n"
        pbt_col   = "cum_pbt" if metric_view == "Cumulative" else "marg_pbt"

        # ── Inner sub-tabs ──────────────────────────────────────────────────
        da_overview, da_grade, da_segments, da_badrate = st.tabs(
            ["Overview", "Grade", "Segments", "Bad Rate"]
        )

        # ---- Overview ----
        with da_overview:
            km1, km2, km3, km4, km5 = st.columns(5)
            km1.metric("Approval", f"{appr_rate:.1%}", f"{A_dash:,} of {N_dash:,}")
            km2.metric("Avg approve grade", f"{avg_grade_appr:.2f}" if A_dash else "—")
            km3.metric("Expected bad", f"{e_bad_rate:.1%}", f"{e_bad_count:,.0f} accts",
                       delta_color="inverse")
            km4.metric("Expected PBT", thb(pbt_total), f"{pbt_pct:.1%} of rev")
            km5.metric("AQI headroom", f"{aqi_headroom*100:+.2f}pp",
                       f"limit grade {aqi_grade_dash}", delta_color="off")

            cpbt_wide = _wide("cum_pbt", _cb_col if groups else None, scale=1 / 1e6)
            lbl = f" — one line per {color_by.lower()}" if groups else ""
            st.caption(f"Cumulative PBT (฿M) vs grade{lbl}")
            if groups:
                st.line_chart(cpbt_wide, height=260)
            else:
                fig_cpbt, ax_cpbt = plt.subplots(figsize=(10, 2.6))
                ax_cpbt.plot(walk_dash["grade"], walk_dash["cum_pbt"] / 1e6,
                             "-o", color=TEAL, lw=2, ms=5, label="Cum PBT")
                ax_cpbt.axhline(0, color=MUTE, lw=0.8)
                if kstar_dash:
                    ax_cpbt.axvline(kstar_dash, color=GOOD, ls="--", lw=1.5, label=f"k*={kstar_dash}")
                if aqi_grade_dash:
                    ax_cpbt.axvline(aqi_grade_dash, color=BAD, ls=":", lw=1.5,
                                    label=f"AQI limit={aqi_grade_dash}")
                ax_cpbt.set_xlabel("Grade (cumulative 1..k)")
                ax_cpbt.set_ylabel("฿M")
                ax_cpbt.set_xticks(walk_dash["grade"].tolist())
                ax_cpbt.legend(fontsize=8)
                ax_cpbt.spines[["top", "right"]].set_visible(False)
                plt.tight_layout()
                st.pyplot(fig_cpbt)
                plt.close(fig_cpbt)

        # ---- Grade ----
        with da_grade:
            mv_label = "Cumulative" if metric_view == "Cumulative" else "Marginal"

            # ── Score charts ────────────────────────────────────────────────
            _has_score = "score" in df_dash.columns and df_dash["score"].nunique() > 1
            sc1, sc2 = st.columns(2)

            with sc1:
                grp_lbl = f" — per {color_by.lower()}" if groups else ""
                st.caption(f"Score distribution{grp_lbl}")
                if _has_score:
                    _smin = float(df_dash["score"].min())
                    _smax = float(df_dash["score"].max())
                    _nbins = min(30, max(10, int((_smax - _smin) // 10)))
                    _edges = np.linspace(_smin, _smax, _nbins + 1)
                    _mids  = ((_edges[:-1] + _edges[1:]) / 2).astype(int)
                    if groups:
                        _sdist = {}
                        for grp in groups:
                            sub = df_dash[df_dash[_cb_col] == grp]["score"]
                            cnts, _ = np.histogram(sub.dropna(), bins=_edges)
                            _sdist[grp] = cnts
                        st.line_chart(
                            pd.DataFrame(_sdist, index=_mids.tolist()), height=230
                        )
                    else:
                        appr_scores = df_appr_dash["score"] if "score" in df_appr_dash.columns else pd.Series(dtype=float)
                        cnts_all, _  = np.histogram(df_dash["score"].dropna(), bins=_edges)
                        cnts_appr, _ = np.histogram(appr_scores.dropna(), bins=_edges)
                        cnts_decl    = cnts_all - cnts_appr
                        fig_sd, ax_sd = plt.subplots(figsize=(5, 2.6))
                        bw = (_smax - _smin) / _nbins * 0.85
                        ax_sd.bar(_mids, cnts_appr, width=bw, color=TEAL, alpha=0.85, label="Approved")
                        ax_sd.bar(_mids, cnts_decl, width=bw, bottom=cnts_appr,
                                  color=MUTE, alpha=0.65, label="Declined")
                        if mode == "score":
                            _sc_thr = min(cutoffs.get(s, 0) for s in dash_segs)
                            ax_sd.axvline(_sc_thr, color=BAD, ls="--", lw=1.5,
                                          label=f"Score cutoff ≥{_sc_thr}")
                        ax_sd.set_xlabel("Score"); ax_sd.set_ylabel("# applicants")
                        ax_sd.legend(fontsize=7)
                        ax_sd.spines[["top", "right"]].set_visible(False)
                        plt.tight_layout(); st.pyplot(fig_sd); plt.close(fig_sd)
                else:
                    st.info("No score data in current filter.")

            with sc2:
                st.caption(f"Avg score per grade{grp_lbl}")
                if _has_score:
                    if groups:
                        _sg_wide = {}
                        for grp in groups:
                            sub = df_dash[df_dash[_cb_col] == grp]
                            _sg_wide[grp] = (
                                sub.groupby("grade")["score"].mean()
                                .reindex(grade_bands).values
                            )
                        st.line_chart(
                            pd.DataFrame(_sg_wide, index=grade_bands), height=230
                        )
                    else:
                        _sg = df_dash.groupby("grade")["score"].mean().reindex(grade_bands)
                        fig_sg2, ax_sg2 = plt.subplots(figsize=(5, 2.6))
                        ax_sg2.plot(_sg.index, _sg.values, "-o", color=TEAL, lw=2, ms=5,
                                    label="Avg score")
                        ax_sg2.axvline(eff_cutoff + 0.5, color=BAD, ls="--", lw=1.5,
                                       label=f"Grade cutoff ≤{eff_cutoff}")
                        ax_sg2.set_xlabel("Grade"); ax_sg2.set_ylabel("Score")
                        ax_sg2.set_xticks(grade_bands)
                        ax_sg2.legend(fontsize=7)
                        ax_sg2.spines[["top", "right"]].set_visible(False)
                        plt.tight_layout(); st.pyplot(fig_sg2); plt.close(fig_sg2)
                else:
                    st.info("No score data in current filter.")

            st.divider()

            # ── Grade charts ─────────────────────────────────────────────────
            ch1, ch2 = st.columns(2)

            with ch1:
                cnt_wide = _wide(count_col, _cb_col if groups else None)
                st.caption(f"{mv_label} count by grade" + (f" — per {color_by.lower()}" if groups else ""))
                if groups:
                    st.line_chart(cnt_wide, height=240)
                else:
                    fig_cnt, ax_cnt = plt.subplots(figsize=(5, 2.6))
                    bar_colors = [TEAL if g <= eff_cutoff else MUTE for g in walk_dash["grade"]]
                    ax_cnt.bar(walk_dash["grade"], walk_dash[count_col], color=bar_colors, alpha=0.85)
                    ax_cnt.axvline(eff_cutoff + 0.5, color=BAD, ls="--", lw=1.5,
                                   label=f"Cutoff ≤{eff_cutoff}")
                    ax_cnt.set_xlabel("Grade"); ax_cnt.set_ylabel("# applicants")
                    ax_cnt.set_xticks(walk_dash["grade"].tolist())
                    ax_cnt.spines[["top", "right"]].set_visible(False)
                    ax_cnt.legend(fontsize=8)
                    plt.tight_layout()
                    st.pyplot(fig_cnt)
                    plt.close(fig_cnt)

            with ch2:
                pbt_wide = _wide(pbt_col, _cb_col if groups else None, scale=1 / 1e3)
                st.caption(f"{mv_label} PBT (฿k) by grade" + (f" — per {color_by.lower()}" if groups else ""))
                if groups:
                    st.line_chart(pbt_wide, height=240)
                else:
                    fig_pbt, ax_pbt = plt.subplots(figsize=(5, 2.6))
                    pbt_colors = [GOOD if v >= 0 else BAD for v in walk_dash[pbt_col]]
                    ax_pbt.bar(walk_dash["grade"], walk_dash[pbt_col] / 1e3, color=pbt_colors, alpha=0.85)
                    ax_pbt.axhline(0, color=MUTE, lw=0.8)
                    if kstar_dash:
                        krow = walk_dash[walk_dash["grade"] == kstar_dash]
                        if not krow.empty:
                            kv = float(krow[pbt_col].values[0]) / 1e3
                            ax_pbt.annotate(
                                f"k*={kstar_dash}",
                                xy=(kstar_dash, kv),
                                xytext=(kstar_dash + 0.4, kv * 1.08 if kv != 0 else 0.5),
                                fontsize=7, color=GOOD,
                                arrowprops=dict(arrowstyle="->", color=GOOD, lw=0.8),
                            )
                    ax_pbt.set_xlabel("Grade"); ax_pbt.set_ylabel("฿k")
                    ax_pbt.set_xticks(walk_dash["grade"].tolist())
                    ax_pbt.spines[["top", "right"]].set_visible(False)
                    plt.tight_layout()
                    st.pyplot(fig_pbt)
                    plt.close(fig_pbt)

            # Styled grade table (always aggregate)
            st.caption(
                f"Teal = approved (grade ≤ {eff_cutoff}).  "
                f"Yellow/bold = k* ({kstar_dash}).  "
                f"AQI-limit grade = {aqi_grade_dash}."
            )
            tbl = walk_dash.copy()
            tbl["marg_bad_acct"]  = tbl["marg_bad_acct"].map("{:.1%}".format)
            tbl["marg_bad_limit"] = tbl["marg_bad_limit"].map("{:.1%}".format)
            tbl["cum_bad_acct"]   = tbl["cum_bad_acct"].map("{:.1%}".format)
            tbl["cum_bad_limit"]  = tbl["cum_bad_limit"].map("{:.1%}".format)
            for c in ("marg_pbt", "cum_pbt", "cum_limit"):
                tbl[c] = tbl[c].map("{:,.0f}".format)
            tbl["cum_bad"] = tbl["cum_bad"].map("{:,.0f}".format)

            def _style_row(row):
                g = int(row["grade"])
                if g == kstar_dash:
                    return ["background-color: #FFF3CD; font-weight: bold"] * len(row)
                if g <= eff_cutoff:
                    return ["background-color: #E6F4F1; color: #0E7C86"] * len(row)
                return [""] * len(row)

            st.dataframe(
                tbl.style.apply(_style_row, axis=1),
                use_container_width=True, hide_index=True, height=280,
            )

        # ---- Segments ----
        with da_segments:
            import altair as alt
            _seg_pool = sorted(df_dash["segment"].unique().tolist())
            _prod_pool = sorted(df_dash["product"].unique().tolist())
            if len(_seg_pool) < 2 and len(_prod_pool) < 2:
                st.info("Select 2+ segments or 2+ products to compare.")
            else:
                # build segment stats table
                seg_rows = []
                for seg in _seg_pool:
                    sdf = df_dash[df_dash["segment"] == seg]
                    k_seg = cutoffs.get(seg, max(grade_bands))
                    stt = seg_stats(sdf, k_seg, mode, PD, E31, ECON)
                    seg_rows.append(dict(
                        segment=seg,
                        approval_pct=round(stt["rate"] * 100, 2),
                        exp_bad_pct=round(stt["e_bad_rate"] * 100, 2),
                        pbt_k=round(stt["pbt"] / 1e3, 1),
                        count=stt["a"],
                    ))
                seg_df = pd.DataFrame(seg_rows)

                # build product stats table
                prod_rows = []
                for prod in _prod_pool:
                    pdf = df_dash[df_dash["product"] == prod]
                    appr_pdf = pdf[approved_mask(pdf, cutoffs, mode)]
                    p = appr_pdf.apply(
                        lambda r: pbt_per_acct(r["product"], r["grade"], PD, ECON), axis=1
                    ).sum()
                    prod_rows.append(dict(product=prod, pbt_k=round(p / 1e3, 1)))
                prod_df = pd.DataFrame(prod_rows)

                sc1, sc2 = st.columns(2)

                # Chart 1 — Approval % vs Expected Bad % grouped bar by segment
                with sc1:
                    st.caption("Approval % vs Expected Bad % — by segment (hover for values)")
                    _melt = seg_df[["segment", "approval_pct", "exp_bad_pct"]].melt(
                        id_vars="segment", var_name="metric", value_name="pct"
                    )
                    _melt["metric"] = _melt["metric"].map(
                        {"approval_pct": "Approval %", "exp_bad_pct": "Exp bad %"}
                    )
                    _c1 = (
                        alt.Chart(_melt)
                        .mark_bar()
                        .encode(
                            x=alt.X("segment:N", title=None,
                                    axis=alt.Axis(labelAngle=-30, labelLimit=120)),
                            y=alt.Y("pct:Q", title="%"),
                            color=alt.Color(
                                "metric:N",
                                scale=alt.Scale(
                                    domain=["Approval %", "Exp bad %"],
                                    range=[TEAL, BAD],
                                ),
                                legend=alt.Legend(title=None, orient="top"),
                            ),
                            xOffset="metric:N",
                            tooltip=[
                                alt.Tooltip("segment:N", title="Segment"),
                                alt.Tooltip("metric:N", title="Metric"),
                                alt.Tooltip("pct:Q", format=".1f", title="%"),
                            ],
                        )
                        .properties(height=260)
                    )
                    st.altair_chart(_c1, use_container_width=True)

                # Chart 2 — PBT by segment + product side by side
                with sc2:
                    st.caption("Expected PBT (฿k) — by segment & product (hover for values)")
                    _c2a = (
                        alt.Chart(seg_df)
                        .mark_bar()
                        .encode(
                            y=alt.Y("segment:N", title="Segment",
                                    axis=alt.Axis(labelLimit=120)),
                            x=alt.X("pbt_k:Q", title="PBT (฿k)"),
                            color=alt.condition(
                                alt.datum.pbt_k >= 0,
                                alt.value(GOOD), alt.value(BAD)
                            ),
                            tooltip=[
                                alt.Tooltip("segment:N", title="Segment"),
                                alt.Tooltip("pbt_k:Q", format=",.1f", title="PBT (฿k)"),
                                alt.Tooltip("count:Q", title="Approved"),
                            ],
                        )
                        .properties(height=160, title="By segment")
                    )
                    _c2b = (
                        alt.Chart(prod_df)
                        .mark_bar()
                        .encode(
                            y=alt.Y("product:N", title="Product"),
                            x=alt.X("pbt_k:Q", title="PBT (฿k)"),
                            color=alt.condition(
                                alt.datum.pbt_k >= 0,
                                alt.value(GOOD), alt.value(BAD)
                            ),
                            tooltip=[
                                alt.Tooltip("product:N", title="Product"),
                                alt.Tooltip("pbt_k:Q", format=",.1f", title="PBT (฿k)"),
                            ],
                        )
                        .properties(height=100, title="By product")
                    )
                    st.altair_chart((_c2a & _c2b).configure_view(strokeWidth=0),
                                    use_container_width=True)

        # ---- Bad Rate ----
        with da_badrate:
            bad_src_col = "marg_bad_acct" if bad_rate_axis == "Account" else "marg_bad_limit"
            if groups:
                bad_wide = _wide(bad_src_col, _cb_col, scale=100.0)
                st.caption(
                    f"Marginal bad rate (%) per grade — one line per {color_by.lower()}"
                )
                st.line_chart(bad_wide, height=300)
            else:
                bad_df = (
                    walk_dash.set_index("grade")[["marg_bad_acct", "marg_bad_limit"]]
                    .rename(columns={"marg_bad_acct": "By account",
                                     "marg_bad_limit": "By credit limit"})
                    * 100
                )
                if bad_rate_axis == "Credit limit":
                    bad_df = bad_df[["By account", "By credit limit"]]
                else:
                    bad_df = bad_df[["By credit limit", "By account"]]
                st.line_chart(bad_df, height=300)
                st.caption(f"Marginal bad rate (%) per grade. Focus: **{bad_rate_axis}**.")


if __name__ == "__main__":
    main()
