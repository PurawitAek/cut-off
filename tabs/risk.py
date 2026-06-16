"""
Concentration & PSI tab — two sub-views:
  Concentration limits : HHI + share-of-book caps on the approved portfolio
                          (segment / product / grade), flags breaches.
  PSI                   : Population Stability Index comparing a Reference
                          population's score/grade distribution against a
                          Current population to detect drift.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from config import TEAL, GOOD, BAD, MUTE
from core import approved_mask


# ── Concentration limits ───────────────────────────────────────────────────────

def _hhi(pct_shares: pd.Series) -> float:
    """Herfindahl-Hirschman Index on the standard 0–10 000 scale (Σ share%²)."""
    return float((pct_shares ** 2).sum())


def _hhi_band(hhi: float) -> str:
    if hhi < 1500:
        return "Unconcentrated"
    if hhi < 2500:
        return "Moderately concentrated"
    return "Highly concentrated"


def _concentration(df, cutoffs, mode, thb):
    st.subheader("Concentration limits — approved portfolio")
    st.markdown(
        "Checks whether the **approved** book is overly concentrated in any single "
        "segment, product, or grade. A book that looks fine in aggregate can still "
        "carry hidden risk if most of the volume sits in one bucket."
    )

    appr = df[approved_mask(df, cutoffs, mode)]
    if appr.empty:
        st.warning("No approved accounts under the current cutoffs.")
        return

    c1, c2 = st.columns(2)
    max_seg_share = c1.slider("Max segment share (%)", 5, 100, 40, key="conc_max_seg") / 100
    max_prod_share = c2.slider("Max product share (%)", 5, 100, 60, key="conc_max_prod") / 100

    seg_cnt = appr["segment"].value_counts()
    seg_shares = seg_cnt / seg_cnt.sum()
    seg_hhi = _hhi(seg_shares * 100)
    seg_breach = seg_shares[seg_shares > max_seg_share]

    prod_cnt = appr["product"].value_counts()
    prod_shares = prod_cnt / prod_cnt.sum()
    prod_hhi = _hhi(prod_shares * 100)
    prod_breach = prod_shares[prod_shares > max_prod_share]

    grade_cnt = appr["grade"].value_counts().sort_index()
    grade_shares = grade_cnt / grade_cnt.sum()
    grade_hhi = _hhi(grade_shares * 100)

    m1, m2, m3 = st.columns(3)
    m1.metric("Segment HHI", f"{seg_hhi:,.0f}", _hhi_band(seg_hhi))
    m2.metric("Product HHI", f"{prod_hhi:,.0f}", _hhi_band(prod_hhi))
    m3.metric("Grade HHI", f"{grade_hhi:,.0f}", _hhi_band(grade_hhi))
    st.caption("HHI bands (DOJ/FTC convention): <1,500 unconcentrated · 1,500–2,500 moderate · >2,500 highly concentrated.")

    if len(seg_breach):
        st.warning("Segment share limit breached: " + ", ".join(
            f"{s} ({v:.0%} > {max_seg_share:.0%})" for s, v in seg_breach.items()))
    if len(prod_breach):
        st.warning("Product share limit breached: " + ", ".join(
            f"{p} ({v:.0%} > {max_prod_share:.0%})" for p, v in prod_breach.items()))
    if not len(seg_breach) and not len(prod_breach):
        st.success("No concentration limits breached.")

    cc1, cc2 = st.columns(2)
    with cc1:
        st.caption("Approved share by segment")
        fig, ax = plt.subplots(figsize=(5, 3))
        colors = [BAD if s in seg_breach.index else TEAL for s in seg_shares.index]
        ax.bar(seg_shares.index.astype(str), seg_shares.values * 100, color=colors, alpha=0.85)
        ax.axhline(max_seg_share * 100, color=BAD, ls="--", lw=1.2, label=f"Limit {max_seg_share:.0%}")
        ax.set_ylabel("% of approved book")
        ax.tick_params(axis="x", rotation=30)
        ax.legend(fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        plt.tight_layout(); st.pyplot(fig); plt.close(fig)

    with cc2:
        st.caption("Approved share by product")
        fig2, ax2 = plt.subplots(figsize=(5, 3))
        colors2 = [BAD if p in prod_breach.index else TEAL for p in prod_shares.index]
        ax2.bar(prod_shares.index.astype(str), prod_shares.values * 100, color=colors2, alpha=0.85)
        ax2.axhline(max_prod_share * 100, color=BAD, ls="--", lw=1.2, label=f"Limit {max_prod_share:.0%}")
        ax2.set_ylabel("% of approved book")
        ax2.tick_params(axis="x", rotation=30)
        ax2.legend(fontsize=8)
        ax2.spines[["top", "right"]].set_visible(False)
        plt.tight_layout(); st.pyplot(fig2); plt.close(fig2)

    st.divider()
    st.caption("Approved share by grade")
    fig3, ax3 = plt.subplots(figsize=(9, 2.6))
    ax3.bar(grade_shares.index.astype(str), grade_shares.values * 100, color=TEAL, alpha=0.85)
    ax3.set_ylabel("% of approved book"); ax3.set_xlabel("Grade")
    ax3.spines[["top", "right"]].set_visible(False)
    plt.tight_layout(); st.pyplot(fig3); plt.close(fig3)

    tbl = pd.DataFrame({
        "Segment": seg_shares.index,
        "Count": seg_cnt.reindex(seg_shares.index).values,
        "Share %": (seg_shares.values * 100).round(2),
    }).sort_values("Share %", ascending=False)
    st.dataframe(tbl, use_container_width=True, hide_index=True)


# ── PSI ───────────────────────────────────────────────────────────────────────

def _psi_bins(ref: pd.Series, n_bins: int = 10) -> np.ndarray:
    """Quantile bin edges from the reference population (standard PSI practice)."""
    qs = np.linspace(0, 1, n_bins + 1)
    edges = np.unique(ref.quantile(qs).values.astype(float))
    if len(edges) < 3:
        edges = np.linspace(float(ref.min()), float(ref.max()), n_bins + 1)
    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def _psi_table(ref: pd.Series, cur: pd.Series, edges) -> pd.DataFrame:
    ref_cnt, _ = np.histogram(ref, bins=edges)
    cur_cnt, _ = np.histogram(cur, bins=edges)
    ref_pct = np.clip(ref_cnt / max(ref_cnt.sum(), 1), 1e-4, None)
    cur_pct = np.clip(cur_cnt / max(cur_cnt.sum(), 1), 1e-4, None)
    contrib = (cur_pct - ref_pct) * np.log(cur_pct / ref_pct)
    labels = [f"[{edges[i]:.0f}, {edges[i+1]:.0f})" for i in range(len(edges) - 1)]
    labels[0] = f"< {edges[1]:.0f}"
    labels[-1] = f"≥ {edges[-2]:.0f}"
    return pd.DataFrame({
        "Bucket": labels,
        "Reference %": ref_pct * 100,
        "Current %": cur_pct * 100,
        "Contribution": contrib,
    })


def _psi_band(psi: float) -> str:
    if psi < 0.1:
        return "Stable — no action needed"
    if psi < 0.25:
        return "Moderate shift — monitor"
    return "Major shift — investigate / recalibrate"


def _psi(df, df_ref, thb):
    st.subheader("Population Stability Index (PSI)")
    st.markdown(
        "Compares the **Reference** population's score/grade distribution against "
        "the **Current** population to detect drift. Buckets are deciles of the "
        "Reference population. PSI < 0.10 = stable, 0.10–0.25 = moderate shift, "
        "> 0.25 = major shift. **Reference** = default dataset (baseline). "
        "**Current** = your uploaded file."
    )

    is_same = df is df_ref
    if is_same:
        st.info("Upload a file via the sidebar to compare it against the default dataset as 'Current'.")

    var = st.radio("Variable", ["Score", "Grade"], horizontal=True, key="psi_var")
    col = "score" if var == "Score" else "grade"
    if col not in df.columns or col not in df_ref.columns:
        st.warning(f"'{col}' column not available in one of the datasets.")
        return

    ref_vals = pd.to_numeric(df_ref[col], errors="coerce").dropna()
    cur_vals = pd.to_numeric(df[col], errors="coerce").dropna()
    if ref_vals.empty or cur_vals.empty:
        st.warning("Not enough data to compute PSI.")
        return

    if col == "score":
        edges = _psi_bins(ref_vals, n_bins=10)
    else:
        g = sorted(ref_vals.unique())
        edges = np.array([-np.inf] + [x + 0.5 for x in g[:-1]] + [np.inf])

    tbl = _psi_table(ref_vals, cur_vals, edges)
    psi_total = tbl["Contribution"].sum()

    m1, m2 = st.columns(2)
    m1.metric("PSI", f"{psi_total:.3f}")
    m2.metric("Interpretation", _psi_band(psi_total))

    fig, ax = plt.subplots(figsize=(9, 3))
    x = np.arange(len(tbl))
    w = 0.38
    ax.bar(x - w / 2, tbl["Reference %"], width=w, color=TEAL, alpha=0.85, label="Reference")
    ax.bar(x + w / 2, tbl["Current %"], width=w, color=GOOD, alpha=0.85, label="Current")
    ax.set_xticks(x); ax.set_xticklabels(tbl["Bucket"], rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("% of population")
    ax.legend(fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout(); st.pyplot(fig); plt.close(fig)

    disp = tbl.copy()
    disp["Reference %"] = disp["Reference %"].map("{:.2f}".format)
    disp["Current %"] = disp["Current %"].map("{:.2f}".format)
    disp["Contribution"] = disp["Contribution"].map("{:.4f}".format)
    st.dataframe(disp, use_container_width=True, hide_index=True)


# ── public entry point ────────────────────────────────────────────────────────

def render_risk(df, cutoffs, mode, thb, df_ref=None):
    SUBS = ["Concentration limits", "PSI"]
    sub = st.segmented_control("View", SUBS, default=SUBS[0], key="risk_section")
    if not sub:
        sub = SUBS[0]

    if sub == "Concentration limits":
        _concentration(df, cutoffs, mode, thb)
    elif sub == "PSI":
        _ref = df_ref if df_ref is not None else df
        _psi(df, _ref, thb)
