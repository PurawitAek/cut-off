from __future__ import annotations
import pandas as pd
import streamlit as st
from core import (
    seg_stats, grade_walk, aqi_limited_grade, grade_walk_portfolio, portfolio_stats,
    resolve_pd, resolve_e31, resolve_econ,
)


def render_cutoff(
    df: pd.DataFrame,
    segments: list,
    mode: str,
    opt_target: str,
    PD: list,
    E31: list,
    ECON: dict,
    grade_bands: list,
    thr: float,
    thb,
    PD_SEG: dict | None = None,
    E31_SEG: dict | None = None,
    ECON_SEG: dict | None = None,
) -> dict:
    """Render the Cutoff & KPIs tab. Returns the cutoffs dict built from sliders."""
    st.subheader("Per-segment cutoff")
    cutoffs: dict = {}
    cols = st.columns(3)
    order = sorted(segments, key=lambda s: df[df["segment"] == s]["grade"].mean())

    for i, seg in enumerate(order):
        sdf = df[df["segment"] == seg]
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
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
                stt = seg_stats(sdf, k, "grade", PD_eff, E31_eff, ECON_eff)
                _, kstar = grade_walk(sdf, PD_eff, E31_eff, ECON_eff, grade_bands)
                alim = aqi_limited_grade(sdf, thr, E31_eff, grade_bands)
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
                stt = seg_stats(sdf, c, "score", PD_eff, E31_eff, ECON_eff)
                st.caption(f"{sdf.iloc[0]['product']} · {len(sdf):,} · approve **{stt['rate']:.0%}** · "
                           f"bad {stt['e_bad_rate']:.1%} · PBT {thb(stt['pbt'])}")

    # Portfolio KPIs
    st.subheader("Portfolio KPIs")
    kpi = portfolio_stats(df, segments, cutoffs, mode, PD, E31, ECON, PD_SEG, E31_SEG, ECON_SEG)
    headroom = thr - kpi["blended_e31"]
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Approval", f"{kpi['rate']:.1%}", f"{int(kpi['A']):,} of {kpi['N']:,}")
    k2.metric("Avg approve grade", f"{kpi['avg_g']:.2f}")
    k3.metric("Expected bad", f"{kpi['bad_rate']:.1%}", f"{kpi['bad']:,.0f} accts",
              delta_color="inverse")
    k4.metric("Expected loss", thb(kpi["loss"]))
    k5.metric("Expected PBT", thb(kpi["pbt"]), f"{kpi['pbt_pct']:.1%} of rev")
    k6.metric("AQI headroom", f"{headroom*100:+.2f}pp", f"{kpi['blended_e31']:.2f}% vs {thr:.2f}%",
              delta_color="off")

    # Grade walk table
    st.subheader("Grade walk — marginal & cumulative (filtered portfolio)")
    walk, kstar = grade_walk_portfolio(df, segments, grade_bands, PD, E31, ECON, PD_SEG, E31_SEG, ECON_SEG)
    disp = walk.copy()
    disp["marg_bad_acct"] = disp["marg_bad_acct"].map("{:.1%}".format)
    disp["marg_bad_limit"] = disp["marg_bad_limit"].map("{:.1%}".format)
    disp["cum_bad_acct"] = disp["cum_bad_acct"].map("{:.1%}".format)
    disp["cum_bad_limit"] = disp["cum_bad_limit"].map("{:.1%}".format)
    for c in ("marg_pbt", "cum_pbt", "cum_limit"):
        disp[c] = disp[c].map("{:,.0f}".format)
    disp["cum_bad"] = disp["cum_bad"].map("{:,.0f}".format)
    disp = disp.drop(columns=["marg_bad", "marg_limit", "marg_limit_bad"])
    st.caption(f"k\\* (grade maximising cumulative PBT) = **{kstar}** "
               f"(resolved per-segment PD/E31/Economics, then combined)")
    st.dataframe(disp, use_container_width=True, hide_index=True)

    return cutoffs
