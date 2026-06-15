from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from config import TEAL, GOOD, BAD, MUTE
from core import approved_mask, econ_of, pd_of


def render_economics(
    df: pd.DataFrame,
    cutoffs: dict,
    mode: str,
    PD: list,
    ECON: dict,
    segments: list,
    thb,
) -> None:
    st.subheader("P&L waterfall — approved population")
    scope = st.selectbox("Scope", ["Whole approved portfolio"] + segments)
    appr = df[approved_mask(df, cutoffs, mode)]
    if scope != "Whole approved portfolio":
        appr = appr[appr["segment"] == scope]

    NII  = appr["product"].map(lambda p: econ_of(p, ECON)["loan"] * econ_of(p, ECON)["eir"]).sum()
    COF  = appr["product"].map(lambda p: econ_of(p, ECON)["loan"] * econ_of(p, ECON)["cof"]).sum()
    OPEX = appr["product"].map(lambda p: econ_of(p, ECON)["opex"]).sum()
    CC   = appr.apply(lambda r: econ_of(r["product"], ECON)["loan"] * pd_of(r["grade"], PD)
                      * econ_of(r["product"], ECON)["lgd"], axis=1).sum()
    REV = NII - COF
    PBT = REV - OPEX - CC

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
    st.pyplot(fig)
    plt.close(fig)

    n = max(len(appr), 1)
    wf = pd.DataFrame({
        "line": ["NII", "COF", "Revenue", "OPEX", "Credit Cost (EL)", "PBT", "Accounts"],
        "THB": [NII, -COF, REV, -OPEX, -CC, PBT, len(appr)],
        "per account": [NII/n, -COF/n, REV/n, -OPEX/n, -CC/n, PBT/n, np.nan],
    })
    st.dataframe(wf, use_container_width=True, hide_index=True)
