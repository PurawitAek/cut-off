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
PD_DEFAULT  = [2, 4, 7, 12, 18, 26, 36, 48, 62, 75]            # % per grade 1..10
E31_DEFAULT = [0.05, 0.10, 0.20, 0.40, 0.70, 1.05, 1.60, 2.40, 3.50, 5.00]  # %Ever31@MOB3
ECON_DEFAULT = {
    "Personal Loan": dict(loan=80000, eir=0.25, cof=0.03, opex=1500, lgd=0.75),
    "Nano Loan":     dict(loan=8000,  eir=0.33, cof=0.04, opex=600,  lgd=0.865),
}
AQI_DEFAULT = dict(cc=14.0, lgd=86.5, pd=86.5, lc=1.10 / 56.13)  # lc ≈ 0.0196 (from worked example)

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
def pd_of(g, PD):       return PD[int(g) - 1] / 100.0
def e31_of(g, E31):     return E31[int(g) - 1]
def econ_of(prod, ECON):
    return ECON.get(prod, next(iter(ECON.values())))

def pbt_per_acct(prod, g, PD, ECON):
    e = econ_of(prod, ECON)
    return e["loan"] * e["eir"] - e["loan"] * e["cof"] - e["opex"] - e["loan"] * pd_of(g, PD) * e["lgd"]

def rev_per_acct(prod, ECON):
    e = econ_of(prod, ECON)
    return e["loan"] * e["eir"] - e["loan"] * e["cof"]


def grade_walk(df: pd.DataFrame, PD, E31, ECON) -> tuple[pd.DataFrame, int]:
    """Walk grades 1..10 best→worst; return per-grade marginal+cumulative table and k*."""
    out = []
    cumN = cumBad = cumLimit = cumLimitBad = cumPBT = 0.0
    kstar = 0
    for g in GRADE_BANDS:
        gl = df[df["grade"] == g]
        n = len(gl)
        pdg = pd_of(g, PD)
        loans = gl["product"].map(lambda p: econ_of(p, ECON)["loan"])
        bad = n * pdg
        limit = loans.sum()
        limit_bad = (loans * pdg).sum()
        marg_pbt = gl["product"].map(lambda p: pbt_per_acct(p, g, PD, ECON)).sum()
        cumN += n; cumBad += bad; cumLimit += limit; cumLimitBad += limit_bad; cumPBT += marg_pbt
        if marg_pbt >= 0 and n > 0:
            kstar = g
        out.append(dict(
            grade=g, n=n,
            marg_bad_acct=pdg, marg_bad_limit=(limit_bad / limit if limit else 0.0),
            marg_pbt=marg_pbt,
            cum_n=int(cumN), cum_bad=cumBad, cum_bad_acct=(cumBad / cumN if cumN else 0.0),
            cum_limit=cumLimit, cum_bad_limit=(cumLimitBad / cumLimit if cumLimit else 0.0),
            cum_pbt=cumPBT,
        ))
    return pd.DataFrame(out), kstar


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

def aqi_limited_grade(df: pd.DataFrame, thr: float, E31) -> int:
    counts = df["grade"].value_counts()
    capN = capE = 0.0
    lim = 0
    for g in GRADE_BANDS:
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
    else:
        df_raw = make_sample_data()
        st.sidebar.caption("Using built-in sample data (1000 rows). Upload a CSV to replace.")
    for col in ("score", "grade"):
        if col in df_raw.columns:
            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

    # ---------- assumptions (sidebar) ----------
    st.sidebar.header("Assumptions")
    with st.sidebar.expander("Grade → PD (%)", expanded=False):
        pd_df = st.data_editor(pd.DataFrame({"grade": GRADE_BANDS, "PD_%": PD_DEFAULT}),
                               hide_index=True, key="pd_ed")
        PD = pd_df["PD_%"].tolist()
    with st.sidebar.expander("Grade → %Ever31@MOB3 (Path-3)", expanded=False):
        e31_df = st.data_editor(pd.DataFrame({"grade": GRADE_BANDS, "Ever31_%": E31_DEFAULT}),
                                hide_index=True, key="e31_ed")
        E31 = e31_df["Ever31_%"].tolist()
    with st.sidebar.expander("Economics per product", expanded=False):
        econ_rows = [dict(product=p, loan=v["loan"], EIR_pct=v["eir"]*100, COF_pct=v["cof"]*100,
                          OPEX=v["opex"], LGD_pct=v["lgd"]*100) for p, v in ECON_DEFAULT.items()]
        econ_df = st.data_editor(pd.DataFrame(econ_rows), hide_index=True, key="econ_ed")
        ECON = {r["product"]: dict(loan=r["loan"], eir=r["EIR_pct"]/100, cof=r["COF_pct"]/100,
                                   opex=r["OPEX"], lgd=r["LGD_pct"]/100) for _, r in econ_df.iterrows()}
    with st.sidebar.expander("AQI parameters", expanded=False):
        AQI = dict(
            cc=st.number_input("%Avg Credit Cost/yr", value=AQI_DEFAULT["cc"], step=0.5),
            lgd=st.number_input("LGD %", value=AQI_DEFAULT["lgd"], step=0.5),
            pd=st.number_input("PD roll 31→91 %", value=AQI_DEFAULT["pd"], step=0.5),
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
    st.markdown("**Active filters:** " + (" · ".join(active) if active else "_none — showing all "
                + f"{len(df_raw):,} applicants_"))

    # ---------- cutoff mode + per-segment cutoffs ----------
    segments = sorted(df["segment"].unique().tolist()) if "segment" in df else ["(all)"]
    thr = aqi_reverse(AQI)["mob3"]

    c1, c2, c3 = st.columns([1.3, 1.3, 1])
    mode = c1.radio("Cutoff mode", ["grade", "score"], horizontal=True,
                    help="grade: approve grades 1..k · score: approve score ≥ threshold")
    opt_target = c2.radio("Optimize", ["Profit k*", "Profit ∧ AQI"], horizontal=True)

    # optimal k per segment (grade mode) used by the Apply button
    def optimal_k(seg_df):
        _, kstar = grade_walk(seg_df, PD, E31, ECON)
        if opt_target.startswith("Profit ∧"):
            return min(kstar, aqi_limited_grade(seg_df, thr, E31))
        return kstar

    if c3.button("Apply optimal to all", use_container_width=True):
        for seg in segments:
            sdf = df[df["segment"] == seg]
            if mode == "grade":
                st.session_state[f"cut_{seg}"] = optimal_k(sdf)
            else:
                # translate optimal grade k into the score threshold = min score among approved grades
                k = optimal_k(sdf)
                appr = sdf[sdf["grade"] <= k]
                st.session_state[f"cut_{seg}"] = int(appr["score"].min()) if len(appr) else 900

    # ---------- tabs ----------
    t_explore, t_cut, t_econ, t_aqi = st.tabs(
        ["Explore", "Cutoff & KPIs", "Economics", "Asset Quality (AQI)"])

    # =========================== EXPLORE ===========================
    with t_explore:
        st.subheader("Data")
        gsearch = st.text_input("Search grid", "")
        show = df
        if gsearch:
            show = df[df.astype(str).apply(lambda r: r.str.contains(gsearch, case=False).any(), axis=1)]
        st.dataframe(show, use_container_width=True, height=380)
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
                    default = int(st.session_state.get(f"cut_{seg}", 6))
                    k = st.slider(f"{seg}", 1, 10, default, key=f"cut_{seg}")
                    cutoffs[seg] = k
                    stt = seg_stats(sdf, k, "grade", PD, E31, ECON)
                    _, kstar = grade_walk(sdf, PD, E31, ECON)
                    alim = aqi_limited_grade(sdf, thr, E31)
                    rec = min(kstar, alim) if opt_target.startswith("Profit ∧") else kstar
                    st.caption(f"{sdf.iloc[0]['product']} · {len(sdf):,} · approve **{stt['rate']:.0%}** · "
                               f"bad {stt['e_bad_rate']:.1%} · PBT {thb(stt['pbt'])}  \n"
                               f"rec k={rec} (k*={kstar}, AQI≤{alim})")
                else:
                    lo, hi = int(sdf["score"].min()), int(sdf["score"].max())
                    default = int(st.session_state.get(f"cut_{seg}", 600))
                    default = min(max(default, lo), hi)
                    c = st.slider(f"{seg}", lo, hi, default, key=f"cut_{seg}")
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
        k3.metric("Expected bad", f"{(bad/A if A else 0):.1%}", f"{bad:,.0f} accts")
        k4.metric("Expected loss", thb(loss))
        k5.metric("Expected PBT", thb(pbt), f"{(pbt/rev if rev else 0):.1%} of rev")
        k6.metric("AQI headroom", f"{headroom*100:+.2f}pp", f"{blended_e31:.2f}% vs {thr:.2f}%")

        # grade walk table
        st.subheader("Grade walk — marginal & cumulative (filtered portfolio)")
        walk, kstar = grade_walk(df, PD, E31, ECON)
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
        for g in GRADE_BANDS:
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
        ax2.text(10, thr, f" AQI {thr:.2f}%", color=BAD, va="bottom", ha="right", fontsize=8)
        ax2.set_xlabel("approve grades 1..k"); ax2.set_xticks(GRADE_BANDS)
        ax2.set_ylabel("blended %Ever31@MOB3")
        ax2.spines[["top", "right"]].set_visible(False)
        st.pyplot(fig2)
        aqi_k = (breach - 1) if breach else 10
        st.caption(f"Blended %Ever31@MOB3 stays under the {thr:.2f}% threshold through grade **{aqi_k}**"
                   + (f", then breaches at grade {breach}." if breach else " (never breaches).")
                   + " Recommended cutoff = the tighter of profit k\\* and this AQI-limited grade.")


if __name__ == "__main__":
    main()
