from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from config import TEAL, GOOD, BAD, MUTE
from core import (grade_walk, aqi_limited_grade, approved_mask,
                  pd_of, e31_of, pbt_per_acct, rev_per_acct, seg_stats, econ_of,
                  grade_walk_portfolio, aqi_limited_grade_portfolio,
                  pd_of_row, e31_of_row, pbt_per_acct_row, rev_per_acct_row,
                  resolve_pd, resolve_e31, resolve_econ)


def render_analytics(
    df: pd.DataFrame,
    cutoffs: dict,
    mode: str,
    PD: list,
    E31: list,
    ECON: dict,
    grade_bands: list,
    thr: float,
    thb,
    PD_SEG: dict | None = None,
    E31_SEG: dict | None = None,
    ECON_SEG: dict | None = None,
) -> None:
    # ── Shared controls ──────────────────────────────────────────────────────
    dc1, dc2, dc3, dc4, dc5 = st.columns([1.4, 1.4, 1, 1, 1])
    with dc1:
        all_segs = sorted(df["segment"].unique().tolist())
        dash_segs = st.multiselect("Segment", all_segs, default=all_segs, key="dash_seg_filter")
    with dc2:
        all_prods = sorted(df["product"].unique().tolist())
        dash_prods = st.multiselect("Product", all_prods, default=all_prods, key="dash_prod_filter")
    with dc3:
        metric_view = st.radio("View", ["Marginal", "Cumulative"], horizontal=True, key="dash_metric_view")
    with dc4:
        bad_rate_axis = st.radio("Bad rate by", ["Account", "Credit limit"], horizontal=True, key="dash_bad_axis")
    with dc5:
        color_by = st.radio("Color by", ["None", "Segment", "Product"], horizontal=True, key="dash_color_by")

    if not dash_segs or not dash_prods:
        st.warning("Select at least one segment and one product.")
        st.stop()

    df_dash = df[df["segment"].isin(dash_segs) & df["product"].isin(dash_prods)].copy()
    if df_dash.empty:
        st.warning("No data for the selected filters.")
        st.stop()

    walk_dash, kstar_dash = grade_walk_portfolio(df_dash, dash_segs, grade_bands, PD, E31, ECON,
                                                 PD_SEG, E31_SEG, ECON_SEG)
    aqi_grade_dash = aqi_limited_grade_portfolio(df_dash, dash_segs, thr, E31, grade_bands, E31_SEG)

    _cb_col = color_by.lower()
    groups = sorted(df_dash[_cb_col].unique().tolist()) if color_by != "None" else None

    def _wide(walk_col, grp_col=None, scale=1.0):
        if grp_col is None:
            return walk_dash.set_index("grade")[[walk_col]] * scale
        out = {}
        for grp in groups:
            sub = df_dash[df_dash[grp_col] == grp]
            if grp_col == "segment":
                PD_eff = resolve_pd(grp, PD, PD_SEG)
                E31_eff = resolve_e31(grp, E31, E31_SEG)
                ECON_eff = resolve_econ(grp, ECON, ECON_SEG)
                w, _ = grade_walk(sub, PD_eff, E31_eff, ECON_eff, grade_bands)
            else:
                sub_segs = sorted(sub["segment"].unique().tolist())
                w, _ = grade_walk_portfolio(sub, sub_segs, grade_bands, PD, E31, ECON,
                                            PD_SEG, E31_SEG, ECON_SEG)
            out[grp] = (w.set_index("grade")[walk_col] * scale).values
        return pd.DataFrame(out, index=walk_dash["grade"].tolist())

    # ── Shared KPI pre-computation ───────────────────────────────────────────
    appr_mask_dash = approved_mask(df_dash, cutoffs, mode)
    df_appr_dash = df_dash[appr_mask_dash]
    N_dash, A_dash = len(df_dash), len(df_appr_dash)
    appr_rate = A_dash / N_dash if N_dash else 0.0
    avg_grade_appr = df_appr_dash["grade"].mean() if A_dash else 0.0
    e_bad_count = df_appr_dash.apply(lambda r: pd_of_row(r, PD, PD_SEG), axis=1).sum() if A_dash else 0.0
    e_bad_rate = e_bad_count / A_dash if A_dash else 0.0
    pbt_total = df_appr_dash.apply(
        lambda r: pbt_per_acct_row(r, PD, ECON, PD_SEG, ECON_SEG), axis=1
    ).sum() if A_dash else 0.0
    rev_total = df_appr_dash.apply(
        lambda r: rev_per_acct_row(r, ECON, ECON_SEG), axis=1
    ).sum() if A_dash else 0.0
    pbt_pct = pbt_total / rev_total if rev_total else 0.0
    blended_e31_dash = (
        df_appr_dash.apply(lambda r: e31_of_row(r, E31, E31_SEG), axis=1).mean() if A_dash else 0.0
    )
    aqi_headroom = thr - blended_e31_dash
    eff_cutoff = (
        min(cutoffs.get(s, max(grade_bands)) for s in dash_segs)
        if dash_segs else max(grade_bands)
    )
    count_col = "cum_n" if metric_view == "Cumulative" else "n"
    pbt_col   = "cum_pbt" if metric_view == "Cumulative" else "marg_pbt"

    # ── Inner sub-tabs ───────────────────────────────────────────────────────
    DA_SUBS = ["Overview", "Grade", "Segments", "Bad Rate"]
    sub = st.segmented_control("View", DA_SUBS, default=DA_SUBS[0], key="dash_section")
    if not sub:
        sub = DA_SUBS[0]

    # ---- Overview ----
    if sub == "Overview":
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
    elif sub == "Grade":
        mv_label = "Cumulative" if metric_view == "Cumulative" else "Marginal"
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
                    st.line_chart(pd.DataFrame(_sdist, index=_mids.tolist()), height=230)
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
                    st.line_chart(pd.DataFrame(_sg_wide, index=grade_bands), height=230)
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
                plt.tight_layout(); st.pyplot(fig_cnt); plt.close(fig_cnt)

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
                plt.tight_layout(); st.pyplot(fig_pbt); plt.close(fig_pbt)

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
    elif sub == "Segments":
        import altair as alt
        _seg_pool = sorted(df_dash["segment"].unique().tolist())
        _prod_pool = sorted(df_dash["product"].unique().tolist())
        if len(_seg_pool) < 2 and len(_prod_pool) < 2:
            st.info("Select 2+ segments or 2+ products to compare.")
        else:
            seg_rows = []
            for seg in _seg_pool:
                sdf = df_dash[df_dash["segment"] == seg]
                k_seg = cutoffs.get(seg, max(grade_bands))
                PD_eff = resolve_pd(seg, PD, PD_SEG)
                E31_eff = resolve_e31(seg, E31, E31_SEG)
                ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
                stt = seg_stats(sdf, k_seg, mode, PD_eff, E31_eff, ECON_eff)
                seg_rows.append(dict(
                    segment=seg,
                    approval_pct=round(stt["rate"] * 100, 2),
                    exp_bad_pct=round(stt["e_bad_rate"] * 100, 2),
                    pbt_k=round(stt["pbt"] / 1e3, 1),
                    count=stt["a"],
                ))
            seg_df = pd.DataFrame(seg_rows)

            prod_rows = []
            for prod in _prod_pool:
                pdf = df_dash[df_dash["product"] == prod]
                appr_pdf = pdf[approved_mask(pdf, cutoffs, mode)]
                p = appr_pdf.apply(
                    lambda r: pbt_per_acct_row(r, PD, ECON, PD_SEG, ECON_SEG), axis=1
                ).sum() if len(appr_pdf) else 0.0
                prod_rows.append(dict(product=prod, pbt_k=round(p / 1e3, 1)))
            prod_df = pd.DataFrame(prod_rows)

            sc1, sc2 = st.columns(2)
            with sc1:
                st.caption("Approval % vs Expected Bad % — by segment (hover for values)")
                _melt = seg_df[["segment", "approval_pct", "exp_bad_pct"]].melt(
                    id_vars="segment", var_name="metric", value_name="pct"
                )
                _melt["metric"] = _melt["metric"].map(
                    {"approval_pct": "Approval %", "exp_bad_pct": "Exp bad %"}
                )
                _c1 = (
                    alt.Chart(_melt).mark_bar().encode(
                        x=alt.X("segment:N", title=None,
                                axis=alt.Axis(labelAngle=-30, labelLimit=120)),
                        y=alt.Y("pct:Q", title="%"),
                        color=alt.Color(
                            "metric:N",
                            scale=alt.Scale(domain=["Approval %", "Exp bad %"],
                                            range=[TEAL, BAD]),
                            legend=alt.Legend(title=None, orient="top"),
                        ),
                        xOffset="metric:N",
                        tooltip=[
                            alt.Tooltip("segment:N", title="Segment"),
                            alt.Tooltip("metric:N", title="Metric"),
                            alt.Tooltip("pct:Q", format=".1f", title="%"),
                        ],
                    ).properties(height=260)
                )
                st.altair_chart(_c1, use_container_width=True)

            with sc2:
                st.caption("Expected PBT (฿k) — by segment & product (hover for values)")
                _c2a = (
                    alt.Chart(seg_df).mark_bar().encode(
                        y=alt.Y("segment:N", title="Segment", axis=alt.Axis(labelLimit=120)),
                        x=alt.X("pbt_k:Q", title="PBT (฿k)"),
                        color=alt.condition(alt.datum.pbt_k >= 0, alt.value(GOOD), alt.value(BAD)),
                        tooltip=[
                            alt.Tooltip("segment:N", title="Segment"),
                            alt.Tooltip("pbt_k:Q", format=",.1f", title="PBT (฿k)"),
                            alt.Tooltip("count:Q", title="Approved"),
                        ],
                    ).properties(height=160, title="By segment")
                )
                _c2b = (
                    alt.Chart(prod_df).mark_bar().encode(
                        y=alt.Y("product:N", title="Product"),
                        x=alt.X("pbt_k:Q", title="PBT (฿k)"),
                        color=alt.condition(alt.datum.pbt_k >= 0, alt.value(GOOD), alt.value(BAD)),
                        tooltip=[
                            alt.Tooltip("product:N", title="Product"),
                            alt.Tooltip("pbt_k:Q", format=",.1f", title="PBT (฿k)"),
                        ],
                    ).properties(height=100, title="By product")
                )
                st.altair_chart((_c2a & _c2b).configure_view(strokeWidth=0),
                                use_container_width=True)

    # ---- Bad Rate ----
    elif sub == "Bad Rate":
        bad_src_col = "marg_bad_acct" if bad_rate_axis == "Account" else "marg_bad_limit"
        if groups:
            bad_wide = _wide(bad_src_col, _cb_col, scale=100.0)
            st.caption(f"Marginal bad rate (%) per grade — one line per {color_by.lower()}")
            st.line_chart(bad_wide, height=300)
        else:
            bad_df = (
                walk_dash.set_index("grade")[["marg_bad_acct", "marg_bad_limit"]]
                .rename(columns={"marg_bad_acct": "By account",
                                 "marg_bad_limit": "By credit limit"})
                * 100
            )
            if bad_rate_axis == "Credit limit":
                bad_df = bad_df[["By credit limit", "By account"]]
            else:
                bad_df = bad_df[["By account", "By credit limit"]]
            st.line_chart(bad_df, height=300)
            st.caption(f"Marginal bad rate (%) per grade. Focus: **{bad_rate_axis}**.")
