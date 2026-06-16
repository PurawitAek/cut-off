from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from config import TEAL, GOOD, BAD, MUTE
from core import (
    approved_mask, econ_of_row, pd_of_row, seg_stats, grade_walk,
    resolve_pd, resolve_econ,
)


# ── P&L waterfall ─────────────────────────────────────────────────────────────

def _waterfall(appr, PD, ECON, thb, PD_SEG=None, ECON_SEG=None):
    if appr.empty:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    def _row_vals(r):
        e = econ_of_row(r, ECON, ECON_SEG)
        pdg = pd_of_row(r, PD, PD_SEG)
        return e["loan"] * e["eir"], e["loan"] * e["cof"], e["opex"], e["loan"] * pdg * e["lgd"]

    vals = appr.apply(_row_vals, axis=1, result_type="expand")
    vals.columns = ["nii", "cof", "opex", "cc"]
    NII, COF, OPEX, CC = vals["nii"].sum(), vals["cof"].sum(), vals["opex"].sum(), vals["cc"].sum()
    REV = NII - COF
    PBT = REV - OPEX - CC
    return NII, COF, OPEX, CC, REV, PBT


def _draw_waterfall(NII, COF, OPEX, CC, REV, PBT):
    fig, ax = plt.subplots(figsize=(9, 3.2))
    labels = ["NII", "COF", "REV", "OPEX", "Credit Cost", "PBT"]
    deltas = [NII, -COF, None, -OPEX, -CC, None]
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
    return fig


# ── Stress test ───────────────────────────────────────────────────────────────

def _stress(df, cutoffs, mode, PD, ECON, segments, thb, PD_SEG=None, ECON_SEG=None):
    st.subheader("Stress test — PD multiplier")
    st.markdown(
        "Scales each segment's PD curve by a multiplier (e.g. 1.5× = 50% higher default rate). "
        "Shows how PBT and expected loss change under stress for each segment."
    )

    mult = st.slider("PD stress multiplier", min_value=0.5, max_value=3.0,
                     value=1.5, step=0.05, format="%.2f×", key="stress_mult")

    rows = []
    base_total = stress_total = 0.0
    for seg in segments:
        sdf = df[df["segment"] == seg]
        cut = cutoffs.get(seg, 10)
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        PD_stressed = [p * mult for p in PD_eff]
        base   = seg_stats(sdf, cut, mode, PD_eff,      [], ECON_eff)
        stress = seg_stats(sdf, cut, mode, PD_stressed, [], ECON_eff)
        rows.append({
            "Segment":         seg,
            "Base PBT (k฿)":  f"{base['pbt']/1e3:,.0f}",
            "Stressed PBT (k฿)": f"{stress['pbt']/1e3:,.0f}",
            "PBT Δ (k฿)":    f"{(stress['pbt']-base['pbt'])/1e3:+.0f}",
            "Base bad %":     f"{base['e_bad_rate']:.1%}",
            "Stressed bad %": f"{stress['e_bad_rate']:.1%}",
        })
        base_total   += base["pbt"]
        stress_total += stress["pbt"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Base portfolio PBT",     thb(base_total))
    m2.metric("Stressed portfolio PBT", thb(stress_total),
              f"{(stress_total-base_total)/1e3:+.0f}k฿", delta_color="inverse")
    m3.metric("PD multiplier applied", f"{mult:.2f}×")

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # PBT sensitivity chart — sweep multipliers 0.5 to 3.0
    mults = [round(m, 2) for m in list(np.arange(0.5, 3.05, 0.1))]
    pbt_curve = []
    for m in mults:
        total = 0.0
        for seg in segments:
            sdf = df[df["segment"] == seg]
            PD_eff = resolve_pd(seg, PD, PD_SEG)
            ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
            PD_m = [p * m for p in PD_eff]
            stt = seg_stats(sdf, cutoffs.get(seg, 10), mode, PD_m, [], ECON_eff)
            total += stt["pbt"]
        pbt_curve.append(total / 1e6)

    fig, ax = plt.subplots(figsize=(8, 3))
    colors = [GOOD if v >= 0 else BAD for v in pbt_curve]
    ax.bar(mults, pbt_curve, width=0.08, color=colors, alpha=0.85)
    ax.axvline(1.0, color=TEAL, ls="--", lw=1.2, label="Base (1×)")
    ax.axvline(mult, color=BAD, ls=":", lw=1.5, label=f"Selected ({mult:.2f}×)")
    ax.axhline(0, color=MUTE, lw=0.8)
    ax.set_xlabel("PD multiplier"); ax.set_ylabel("Portfolio PBT (฿M)")
    ax.legend(fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout(); st.pyplot(fig); plt.close(fig)


# ── Break-even ────────────────────────────────────────────────────────────────

def _breakeven(df, PD, ECON, grade_bands, segments, thb, PD_SEG=None, ECON_SEG=None):
    st.subheader("Break-even analysis — minimum approval rate for PBT ≥ 0")
    st.markdown(
        "For each segment, this finds the tightest grade cutoff where "
        "the segment's expected PBT turns positive."
    )

    g_max = max(grade_bands) if grade_bands else 10
    rows = []
    for seg in segments:
        sdf = df[df["segment"] == seg]
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        be_grade = None
        for k in range(1, g_max + 1):
            stt = seg_stats(sdf, k, "grade", PD_eff, [], ECON_eff)
            if stt["pbt"] >= 0:
                be_grade = k
                be_rate  = stt["rate"]
                be_pbt   = stt["pbt"]
                break
        rows.append({
            "Segment":           seg,
            "Break-even grade":  be_grade if be_grade else "Never profitable",
            "Min approval rate": f"{be_rate:.1%}" if be_grade else "—",
            "PBT at break-even": thb(be_pbt) if be_grade else "—",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # PBT by grade for each segment in a grid
    n_segs = len(segments)
    if n_segs == 0:
        return
    cols = st.columns(min(3, n_segs))
    for i, seg in enumerate(segments):
        sdf = df[df["segment"] == seg]
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        pbts = []
        for k in range(1, g_max + 1):
            stt = seg_stats(sdf, k, "grade", PD_eff, [], ECON_eff)
            pbts.append(stt["pbt"] / 1e3)
        with cols[i % 3]:
            fig, ax = plt.subplots(figsize=(3, 2))
            colors = [GOOD if v >= 0 else BAD for v in pbts]
            ax.bar(range(1, g_max + 1), pbts, color=colors, alpha=0.85)
            ax.axhline(0, color=MUTE, lw=0.8)
            ax.set_title(seg, fontsize=8)
            ax.set_xlabel("Grade k", fontsize=7)
            ax.set_ylabel("PBT k฿", fontsize=7)
            ax.tick_params(labelsize=6)
            ax.spines[["top", "right"]].set_visible(False)
            plt.tight_layout(); st.pyplot(fig); plt.close(fig)


# ── public entry point ────────────────────────────────────────────────────────

def render_economics(df, cutoffs, mode, PD, ECON, grade_bands, segments, thb,
                      PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    SUBS = ["P&L Waterfall", "Stress Test", "Break-even"]
    sub = st.segmented_control("View", SUBS, default=SUBS[0], key="econ_section")
    if not sub:
        sub = SUBS[0]

    if sub == "P&L Waterfall":
        st.subheader("P&L waterfall — approved population")
        scope = st.selectbox("Scope", ["Whole approved portfolio"] + segments)
        appr = df[approved_mask(df, cutoffs, mode)]
        if scope != "Whole approved portfolio":
            appr = appr[appr["segment"] == scope]

        NII, COF, OPEX, CC, REV, PBT = _waterfall(appr, PD, ECON, thb, PD_SEG, ECON_SEG)
        fig = _draw_waterfall(NII, COF, OPEX, CC, REV, PBT)
        st.pyplot(fig); plt.close(fig)

        n = max(len(appr), 1)
        wf = pd.DataFrame({
            "line": ["NII", "COF", "Revenue", "OPEX", "Credit Cost (EL)", "PBT", "Accounts"],
            "THB":         [NII, -COF, REV, -OPEX, -CC, PBT, len(appr)],
            "per account": [NII/n, -COF/n, REV/n, -OPEX/n, -CC/n, PBT/n, np.nan],
        })
        st.dataframe(wf, use_container_width=True, hide_index=True)

    elif sub == "Stress Test":
        _stress(df, cutoffs, mode, PD, ECON, segments, thb, PD_SEG, ECON_SEG)

    elif sub == "Break-even":
        _breakeven(df, PD, ECON, grade_bands, segments, thb, PD_SEG, ECON_SEG)
