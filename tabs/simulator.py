"""
Simulator tab — four sub-tabs:
  What-if            : shadow sliders show delta KPIs without committing.
  Champion vs Chal.  : snapshot current policy, define challenger, compare.
  Efficiency Frontier: sweep uniform grade cutoff k across all segments.
  Data Comparison    : default dataset vs uploaded dataset under the same cutoffs.
"""
from __future__ import annotations
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st
from config import TEAL, GOOD, BAD, MUTE
from core import seg_stats, grade_walk, aqi_limited_grade, portfolio_stats, resolve_pd, resolve_e31, resolve_econ


# ── shared helper ─────────────────────────────────────────────────────────────

def _portfolio_kpis(df, cutoffs, mode, PD, E31, ECON, segments, thr, PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    kpi = portfolio_stats(df, segments, cutoffs, mode, PD, E31, ECON, PD_SEG, E31_SEG, ECON_SEG)
    kpi["headroom"] = thr - kpi["blended_e31"]
    return kpi


def _seg_sliders(df, segments, cutoffs, mode, grade_bands, key_prefix):
    """Render per-segment sliders with a given key prefix; return cutoffs dict."""
    g_max = max(grade_bands) if grade_bands else 10
    result: dict = {}
    cols = st.columns(3)
    order = sorted(segments, key=lambda s: df[df["segment"] == s]["grade"].mean())
    for i, seg in enumerate(order):
        sdf = df[df["segment"] == seg]
        with cols[i % 3]:
            if mode == "grade":
                default = min(max(cutoffs.get(seg, min(6, g_max)), 1), g_max)
                result[seg] = st.slider(seg, 1, g_max, default, key=f"{key_prefix}_{seg}")
            else:
                lo, hi = int(sdf["score"].min()), int(sdf["score"].max())
                default = min(max(cutoffs.get(seg, 600), lo), hi)
                result[seg] = st.slider(seg, lo, hi, default, key=f"{key_prefix}_{seg}")
    return result


def _comparison_table(cur, sim, thb):
    rows = [
        ("Approval rate",    f"{cur['rate']:.1%}",      f"{sim['rate']:.1%}",
         f"{(sim['rate']-cur['rate'])*100:+.1f}pp"),
        ("Avg approve grade",f"{cur['avg_g']:.2f}",     f"{sim['avg_g']:.2f}",
         f"{sim['avg_g']-cur['avg_g']:+.2f}"),
        ("Expected bad rate",f"{cur['bad_rate']:.1%}",  f"{sim['bad_rate']:.1%}",
         f"{(sim['bad_rate']-cur['bad_rate'])*100:+.1f}pp"),
        ("Expected PBT",     thb(cur['pbt']),            thb(sim['pbt']),
         f"{(sim['pbt']-cur['pbt'])/1e3:+.0f}k฿"),
        ("AQI headroom",
         f"{cur['headroom']*100:+.2f}pp",
         f"{sim['headroom']*100:+.2f}pp",
         f"{(sim['headroom']-cur['headroom'])*100:+.2f}pp"),
    ]
    h = st.columns([2, 1.5, 1.5, 1.2])
    for col, label in zip(h, ["Metric", "Current", "Scenario / Challenger", "Δ"]):
        col.markdown(f"**{label}**")
    for metric, c_val, s_val, delta in rows:
        r = st.columns([2, 1.5, 1.5, 1.2])
        r[0].write(metric); r[1].write(c_val); r[2].write(s_val); r[3].write(delta)


# ── What-if ───────────────────────────────────────────────────────────────────

def _whatif(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
            PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    st.markdown(
        "Adjust **shadow cutoffs** below — nothing is committed to the live "
        "Cutoff tab until you click **Apply to live**."
    )
    sim_cutoffs = _seg_sliders(df, segments, cutoffs, mode, grade_bands, "sim")

    if st.button("Apply scenario to live cutoffs", type="primary", key="sim_apply"):
        for seg, val in sim_cutoffs.items():
            st.session_state[f"cut_{seg}"] = val
        st.success("Applied — switch to the Cutoff & KPIs tab to confirm.")

    st.divider()
    st.subheader("Portfolio KPI comparison")
    cur = _portfolio_kpis(df, cutoffs,     mode, PD, E31, ECON, segments, thr, PD_SEG, E31_SEG, ECON_SEG)
    sim = _portfolio_kpis(df, sim_cutoffs, mode, PD, E31, ECON, segments, thr, PD_SEG, E31_SEG, ECON_SEG)
    _comparison_table(cur, sim, thb)

    st.divider()
    st.subheader("Per-segment delta")
    rows = []
    for seg in segments:
        sdf = df[df["segment"] == seg]
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        c = seg_stats(sdf, cutoffs.get(seg, 10),     mode, PD_eff, E31_eff, ECON_eff)
        s = seg_stats(sdf, sim_cutoffs.get(seg, 10), mode, PD_eff, E31_eff, ECON_eff)
        rows.append({
            "Segment":         seg,
            "Current cutoff":  cutoffs.get(seg, "—"),
            "Scenario cutoff": sim_cutoffs.get(seg, "—"),
            "Approval Δ":      f"{(s['rate']-c['rate'])*100:+.1f}pp",
            "Bad rate Δ":      f"{(s['e_bad_rate']-c['e_bad_rate'])*100:+.1f}pp",
            "PBT Δ (k฿)":     f"{(s['pbt']-c['pbt'])/1e3:+.0f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Champion vs Challenger ────────────────────────────────────────────────────

def _champ_chal(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                 PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    st.markdown(
        "**Champion** = frozen snapshot of any previous policy.  \n"
        "**Challenger** = a new policy you define below."
    )

    col_freeze, col_promote = st.columns(2)
    if col_freeze.button("Freeze current cutoffs as Champion"):
        st.session_state["champion"] = dict(cutoffs)
        st.success("Champion frozen.")

    champion: dict = st.session_state.get("champion", {})
    if not champion:
        st.info("No champion frozen yet — set your cutoffs in the **Cutoff & KPIs** tab, "
                "then click **Freeze current cutoffs as Champion**.")
        return

    st.caption("Champion: " + "  |  ".join(f"{s} = {v}" for s, v in champion.items()))
    st.divider()
    st.subheader("Define Challenger cutoffs")
    chal_cutoffs = _seg_sliders(df, segments, champion, mode, grade_bands, "chal")

    if col_promote.button("Promote Challenger → live", type="primary"):
        for seg, val in chal_cutoffs.items():
            st.session_state[f"cut_{seg}"] = val
        st.success("Challenger promoted to live cutoffs.")

    st.divider()
    st.subheader("Head-to-head comparison")
    champ_kpi = _portfolio_kpis(df, champion,     mode, PD, E31, ECON, segments, thr, PD_SEG, E31_SEG, ECON_SEG)
    chal_kpi  = _portfolio_kpis(df, chal_cutoffs, mode, PD, E31, ECON, segments, thr, PD_SEG, E31_SEG, ECON_SEG)
    _comparison_table(champ_kpi, chal_kpi, thb)

    st.divider()
    st.subheader("Per-segment detail")
    rows = []
    for seg in segments:
        sdf = df[df["segment"] == seg]
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        ch = seg_stats(sdf, champion.get(seg, 10),     mode, PD_eff, E31_eff, ECON_eff)
        ca = seg_stats(sdf, chal_cutoffs.get(seg, 10), mode, PD_eff, E31_eff, ECON_eff)
        rows.append({
            "Segment":        seg,
            "Champ cut":      champion.get(seg, "—"),
            "Chal cut":       chal_cutoffs.get(seg, "—"),
            "Champ approval": f"{ch['rate']:.0%}",
            "Chal approval":  f"{ca['rate']:.0%}",
            "Champ bad":      f"{ch['e_bad_rate']:.1%}",
            "Chal bad":       f"{ca['e_bad_rate']:.1%}",
            "Champ PBT k฿":  f"{ch['pbt']/1e3:,.0f}",
            "Chal PBT k฿":   f"{ca['pbt']/1e3:,.0f}",
            "PBT Δ k฿":      f"{(ca['pbt']-ch['pbt'])/1e3:+.0f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Efficiency Frontier ───────────────────────────────────────────────────────

def _frontier(df, segments, mode, PD, E31, ECON, grade_bands, thr, thb,
              PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    st.markdown(
        "Each point = a **uniform grade cutoff k** applied to all segments. "
        "Colour shows expected PBT. Use this to identify where tightening "
        "the cutoff stops paying off."
    )

    g_max = max(grade_bands) if grade_bands else 10
    points = []
    for k in range(1, g_max + 1):
        kpi = _portfolio_kpis(df, {s: k for s in segments},
                              "grade", PD, E31, ECON, segments, thr, PD_SEG, E31_SEG, ECON_SEG)
        points.append(dict(k=k, approval=kpi["rate"], bad=kpi["bad_rate"],
                           pbt=kpi["pbt"], headroom=kpi["headroom"]))
    fr = pd.DataFrame(points)

    left, right = st.columns([3, 2])
    with left:
        fig, ax = plt.subplots(figsize=(7, 4))
        sc = ax.scatter(fr["approval"]*100, fr["bad"]*100,
                        c=fr["pbt"]/1e6, cmap="RdYlGn", s=130, zorder=3)
        for _, row in fr.iterrows():
            ax.annotate(f"k={int(row['k'])}",
                        (row["approval"]*100, row["bad"]*100),
                        fontsize=7, xytext=(4, 4), textcoords="offset points")
        ax.plot(fr["approval"]*100, fr["bad"]*100, color=MUTE, lw=1, ls="--", zorder=2)
        plt.colorbar(sc, ax=ax, label="PBT (฿M)")
        ax.set_xlabel("Approval rate (%)"); ax.set_ylabel("Bad rate (%)")
        ax.set_title("Approval vs Bad-rate frontier")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(True, alpha=0.25)
        plt.tight_layout(); st.pyplot(fig); plt.close(fig)

    with right:
        st.caption("PBT by grade cutoff k")
        fig2, ax2 = plt.subplots(figsize=(3.5, 4))
        colors = [GOOD if v >= 0 else BAD for v in fr["pbt"]]
        ax2.barh(fr["k"], fr["pbt"]/1e6, color=colors, alpha=0.85)
        ax2.axvline(0, color=MUTE, lw=0.8)
        ax2.set_xlabel("PBT (฿M)"); ax2.set_ylabel("Grade cutoff k")
        ax2.set_yticks(fr["k"].tolist())
        ax2.spines[["top", "right"]].set_visible(False)
        plt.tight_layout(); st.pyplot(fig2); plt.close(fig2)

    st.divider()
    disp = fr.copy()
    disp["approval"]  = disp["approval"].map("{:.1%}".format)
    disp["bad"]       = disp["bad"].map("{:.2%}".format)
    disp["pbt"]       = disp["pbt"].map("{:,.0f}".format)
    disp["headroom"]  = disp["headroom"].map("{:+.3f}".format)
    disp.columns = ["Grade k", "Approval rate", "Bad rate", "PBT (฿)", "AQI headroom"]
    st.dataframe(disp, use_container_width=True, hide_index=True)


# ── Data Comparison ───────────────────────────────────────────────────────────

def _data_compare(df, df_ref, segments, cutoffs, mode, PD, E31, ECON, thr, thb,
                   PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    is_same = df is df_ref
    if is_same:
        st.info(
            "Upload a file via the sidebar to compare it against the default dataset. "
            "Currently both datasets are identical."
        )

    st.markdown(
        "Applies the **same cutoffs** to both datasets and compares portfolio KPIs side by side. "
        "**Default** = baseline (default_input.xlsx). **Uploaded** = your file."
    )

    # shared segments: only segments that exist in the reference data
    ref_segs = [s for s in segments if s in df_ref["segment"].unique()]
    upl_segs = [s for s in segments if s in df["segment"].unique()]

    # portfolio-level KPIs
    ref_kpi = _portfolio_kpis(df_ref, cutoffs, mode, PD, E31, ECON, ref_segs, thr, PD_SEG, E31_SEG, ECON_SEG)
    upl_kpi = _portfolio_kpis(df,     cutoffs, mode, PD, E31, ECON, upl_segs, thr, PD_SEG, E31_SEG, ECON_SEG)

    # ── top metrics ──
    st.subheader("Portfolio KPIs")
    labels = [
        ("Total applicants",   f"{ref_kpi['N']:,}",                    f"{upl_kpi['N']:,}"),
        ("Approval rate",      f"{ref_kpi['rate']:.1%}",               f"{upl_kpi['rate']:.1%}"),
        ("Avg approved grade", f"{ref_kpi['avg_g']:.2f}",              f"{upl_kpi['avg_g']:.2f}"),
        ("Expected bad rate",  f"{ref_kpi['bad_rate']:.1%}",           f"{upl_kpi['bad_rate']:.1%}"),
        ("Expected PBT",       thb(ref_kpi['pbt']),                    thb(upl_kpi['pbt'])),
        ("PBT per applicant",  thb(ref_kpi['pbt']/max(ref_kpi['N'],1)), thb(upl_kpi['pbt']/max(upl_kpi['N'],1))),
        ("AQI headroom",       f"{ref_kpi['headroom']*100:+.2f}pp",    f"{upl_kpi['headroom']*100:+.2f}pp"),
    ]
    h = st.columns([2, 1.8, 1.8, 1.4])
    for col, lbl in zip(h, ["Metric", "Default (baseline)", "Uploaded", "Δ (Uploaded − Default)"]):
        col.markdown(f"**{lbl}**")
    for metric, r_val, u_val in labels:
        row = st.columns([2, 1.8, 1.8, 1.4])
        row[0].write(metric)
        row[1].write(r_val)
        row[2].write(u_val)
        # compute numeric delta where possible
        try:
            r_n = float(r_val.replace(",", "").replace("฿", "").replace("%", "")
                        .replace("+", "").replace("pp", ""))
            u_n = float(u_val.replace(",", "").replace("฿", "").replace("%", "")
                        .replace("+", "").replace("pp", ""))
            delta = u_n - r_n
            sign  = "+" if delta >= 0 else ""
            row[3].write(f"{sign}{delta:,.2f}")
        except Exception:
            row[3].write("—")

    st.divider()

    # ── per-segment breakdown ──
    st.subheader("Per-segment breakdown")
    all_segs = sorted(set(ref_segs) | set(upl_segs))
    rows = []
    for seg in all_segs:
        r_sdf = df_ref[df_ref["segment"] == seg]
        u_sdf = df[df["segment"] == seg]
        cut = cutoffs.get(seg, 10)
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)

        def _stt(sdf):
            if sdf.empty:
                return None
            return seg_stats(sdf, cut, mode, PD_eff, E31_eff, ECON_eff)

        r = _stt(r_sdf)
        u = _stt(u_sdf)
        rows.append({
            "Segment":              seg,
            "Default n":            f"{len(r_sdf):,}" if r else "—",
            "Uploaded n":           f"{len(u_sdf):,}" if u else "—",
            "Default approval":     f"{r['rate']:.0%}"        if r else "—",
            "Uploaded approval":    f"{u['rate']:.0%}"        if u else "—",
            "Default bad %":        f"{r['e_bad_rate']:.1%}"  if r else "—",
            "Uploaded bad %":       f"{u['e_bad_rate']:.1%}"  if u else "—",
            "Default PBT (k฿)":    f"{r['pbt']/1e3:,.0f}"   if r else "—",
            "Uploaded PBT (k฿)":   f"{u['pbt']/1e3:,.0f}"   if u else "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ── score distribution comparison ──
    if "score" in df.columns and "score" in df_ref.columns:
        st.subheader("Score distribution — Default vs Uploaded")
        from config import TEAL, GOOD

        fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), sharey=False)
        for ax, (data, label, color) in zip(axes, [
            (df_ref, "Default (baseline)", TEAL),
            (df,     "Uploaded",           GOOD),
        ]):
            ax.hist(data["score"], bins=30, color=color, alpha=0.8, edgecolor="white", linewidth=0.4)
            ax.axvline(data["score"].mean(), color="#D9483B", ls="--", lw=1.2,
                       label=f"mean {data['score'].mean():.0f}")
            ax.set_title(label, fontsize=10)
            ax.set_xlabel("Score"); ax.set_ylabel("Count")
            ax.legend(fontsize=8)
            ax.spines[["top", "right"]].set_visible(False)
        plt.tight_layout()
        st.pyplot(fig); plt.close(fig)


# ── public entry point ────────────────────────────────────────────────────────

def render_simulator(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                     df_ref=None, PD_SEG=None, E31_SEG=None, ECON_SEG=None):
    SUBS = ["What-if", "Champion vs Challenger", "Efficiency Frontier", "Data Comparison"]
    sub = st.segmented_control("View", SUBS, default=SUBS[0], key="sim_section")
    if not sub:
        sub = SUBS[0]

    if sub == "What-if":
        _whatif(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                PD_SEG, E31_SEG, ECON_SEG)
    elif sub == "Champion vs Challenger":
        _champ_chal(df, segments, cutoffs, mode, PD, E31, ECON, grade_bands, thr, thb,
                    PD_SEG, E31_SEG, ECON_SEG)
    elif sub == "Efficiency Frontier":
        _frontier(df, segments, mode, PD, E31, ECON, grade_bands, thr, thb,
                  PD_SEG, E31_SEG, ECON_SEG)
    elif sub == "Data Comparison":
        _ref = df_ref if df_ref is not None else df
        _data_compare(df, _ref, segments, cutoffs, mode, PD, E31, ECON, thr, thb,
                      PD_SEG, E31_SEG, ECON_SEG)
