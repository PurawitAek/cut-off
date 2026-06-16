from __future__ import annotations
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from config import AQI_DEFAULT, TEAL, BAD
from core import aqi_reverse, aqi_forward, e31_of, resolve_e31


def render_aqi(
    df: pd.DataFrame,
    AQI: dict,
    E31: list,
    grade_bands: list,
    thr: float,
    E31_SEG: dict | None = None,
) -> None:
    r = aqi_reverse(AQI)
    st.subheader("Reverse — target → threshold")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("%Avg Credit Cost/yr", f"{AQI['cc']:.2f}%")
    a2.metric("×3 → Cum 3yr", f"{r['cum']:.2f}%")
    a3.metric("÷LGD → Ever91 3yr", f"{r['e91']:.2f}%")
    a4.metric("÷PD → Ever31 3yr", f"{r['e31']:.2f}%")
    a5.metric("×curve → Ever31@MOB3", f"{r['mob3']:.2f}%")

    ref = [49.14, 65.52, 75.75, 2.48]
    got = [r["cum"], r["e91"], r["e31"], r["mob3"]]
    at_default = (abs(AQI["cc"] - AQI_DEFAULT["cc"]) < 1e-9
                  and abs(AQI["lgd"] - AQI_DEFAULT["lgd"]) < 1e-9
                  and abs(AQI["pd"] - AQI_DEFAULT["pd"]) < 1e-9
                  and abs(AQI["lc"] - AQI_DEFAULT["lc"]) < 1e-9)
    if at_default:
        ok = all(abs(a - b) < 0.05 for a, b in zip(got, ref))
        (st.success if ok else st.error)(
            f"{'✓' if ok else '✗'} unit test  {AQI_DEFAULT['cc']} → 49.14 → 65.52 → 75.75 → 2.48")
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
    segments = sorted(df["segment"].unique().tolist())
    seg_counts = {seg: df[df["segment"] == seg]["grade"].value_counts() for seg in segments}
    capN = capE = 0.0; series = []; breach = None
    for g in grade_bands:
        for seg in segments:
            c = int(seg_counts[seg].get(g, 0))
            E31_eff = resolve_e31(seg, E31, E31_SEG)
            capN += c; capE += c * e31_of(g, E31_eff)
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
    plt.close(fig2)
    aqi_k = (breach - 1) if breach else g_last
    st.caption(f"Blended %Ever31@MOB3 stays under the {thr:.2f}% threshold through grade **{aqi_k}**"
               + (f", then breaches at grade {breach}." if breach else " (never breaches).")
               + " Recommended cutoff = the tighter of profit k\\* and this AQI-limited grade.")
