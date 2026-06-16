"""Pure credit-decisioning calculations — no Streamlit dependency."""
from __future__ import annotations
import numpy as np
import pandas as pd
from config import GRADE_BANDS


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


def resolve_pd(seg, PD, PD_SEG=None):
    """Segment-specific PD curve override, else the global/product default."""
    return (PD_SEG or {}).get(seg, PD)


def resolve_e31(seg, E31, E31_SEG=None):
    """Segment-specific E31 curve override, else the global/product default."""
    return (E31_SEG or {}).get(seg, E31)


def resolve_econ(seg, ECON, ECON_SEG=None):
    """Segment-specific economics override, else the global/product default."""
    return (ECON_SEG or {}).get(seg, ECON)


def pd_of_row(row, PD, PD_SEG=None):
    return pd_of(row["grade"], resolve_pd(row["segment"], PD, PD_SEG))


def e31_of_row(row, E31, E31_SEG=None):
    return e31_of(row["grade"], resolve_e31(row["segment"], E31, E31_SEG))


def econ_of_row(row, ECON, ECON_SEG=None):
    return econ_of(row["product"], resolve_econ(row["segment"], ECON, ECON_SEG))


def pbt_per_acct_row(row, PD, ECON, PD_SEG=None, ECON_SEG=None):
    e = econ_of_row(row, ECON, ECON_SEG)
    pdg = pd_of_row(row, PD, PD_SEG)
    return e["loan"] * e["eir"] - e["loan"] * e["cof"] - e["opex"] - e["loan"] * pdg * e["lgd"]


def rev_per_acct_row(row, ECON, ECON_SEG=None):
    e = econ_of_row(row, ECON, ECON_SEG)
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
            marg_bad=bad, marg_limit=limit, marg_limit_bad=limit_bad,
            marg_pbt=marg_pbt,
            cum_n=int(cumN), cum_bad=cumBad, cum_bad_acct=(cumBad / cumN if cumN else 0.0),
            cum_limit=cumLimit, cum_bad_limit=(cumLimitBad / cumLimit if cumLimit else 0.0),
            cum_pbt=cumPBT,
        ))
    df_out = pd.DataFrame(out)
    kstar = 0
    if not df_out.empty:
        active = df_out[df_out["n"] > 0]
        if not active.empty and active["cum_pbt"].max() > 0:
            kstar = int(df_out.loc[active["cum_pbt"].idxmax(), "grade"])
    return df_out, kstar


def grade_walk_portfolio(df: pd.DataFrame, segments, grade_bands=None,
                          PD=None, E31=None, ECON=None,
                          PD_SEG=None, E31_SEG=None, ECON_SEG=None) -> tuple[pd.DataFrame, int]:
    """grade_walk across a multi-segment dataframe, resolving PD/E31/ECON per segment.

    Runs grade_walk once per segment (each with its own resolved tables), then
    sums the marginal raw values per grade across segments and recomputes the
    cumulative columns — mirrors how grade_walk combines grades within one segment.
    """
    if grade_bands is None:
        grade_bands = GRADE_BANDS
    acc = {g: dict(n=0, bad=0.0, limit=0.0, limit_bad=0.0, pbt=0.0) for g in grade_bands}
    for seg in segments:
        sdf = df[df["segment"] == seg]
        if sdf.empty:
            continue
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        walk_seg, _ = grade_walk(sdf, PD_eff, E31_eff, ECON_eff, grade_bands)
        for _, row in walk_seg.iterrows():
            g = row["grade"]
            acc[g]["n"]         += row["n"]
            acc[g]["bad"]       += row["marg_bad"]
            acc[g]["limit"]     += row["marg_limit"]
            acc[g]["limit_bad"] += row["marg_limit_bad"]
            acc[g]["pbt"]       += row["marg_pbt"]
    out = []
    cumN = cumBad = cumLimit = cumLimitBad = cumPBT = 0.0
    for g in grade_bands:
        a = acc[g]
        n, bad, limit, limit_bad, marg_pbt = a["n"], a["bad"], a["limit"], a["limit_bad"], a["pbt"]
        cumN += n; cumBad += bad; cumLimit += limit; cumLimitBad += limit_bad; cumPBT += marg_pbt
        out.append(dict(
            grade=g, n=n,
            marg_bad_acct=(bad / n if n else 0.0), marg_bad_limit=(limit_bad / limit if limit else 0.0),
            marg_bad=bad, marg_limit=limit, marg_limit_bad=limit_bad,
            marg_pbt=marg_pbt,
            cum_n=int(cumN), cum_bad=cumBad, cum_bad_acct=(cumBad / cumN if cumN else 0.0),
            cum_limit=cumLimit, cum_bad_limit=(cumLimitBad / cumLimit if cumLimit else 0.0),
            cum_pbt=cumPBT,
        ))
    df_out = pd.DataFrame(out)
    kstar = 0
    if not df_out.empty:
        active = df_out[df_out["n"] > 0]
        if not active.empty and active["cum_pbt"].max() > 0:
            kstar = int(df_out.loc[active["cum_pbt"].idxmax(), "grade"])
    return df_out, kstar


def approved_mask(df: pd.DataFrame, cutoffs: dict, mode: str) -> pd.Series:
    """Boolean mask of approved rows given per-segment cutoffs and the cutoff mode."""
    if mode == "grade":
        return df.apply(lambda r: r["grade"] <= cutoffs.get(r["segment"], 10), axis=1)
    else:
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


def portfolio_stats(df: pd.DataFrame, segments, cutoffs: dict, mode: str, PD, E31, ECON,
                     PD_SEG=None, E31_SEG=None, ECON_SEG=None) -> dict:
    """Aggregate seg_stats across segments, resolving PD/E31/ECON per segment."""
    A = bad = loss = pbt = rev = gsum = e31w = 0.0
    N = len(df)
    for seg in segments:
        sdf = df[df["segment"] == seg]
        if sdf.empty:
            continue
        PD_eff = resolve_pd(seg, PD, PD_SEG)
        E31_eff = resolve_e31(seg, E31, E31_SEG)
        ECON_eff = resolve_econ(seg, ECON, ECON_SEG)
        stt = seg_stats(sdf, cutoffs.get(seg, 10), mode, PD_eff, E31_eff, ECON_eff)
        A    += stt["a"];           bad  += stt["e_bad"]
        loss += stt["e_loss"];      pbt  += stt["pbt"]
        rev  += stt["rev"]
        gsum  += stt["avg_g"]       * stt["a"]
        e31w  += stt["blended_e31"] * stt["a"]
    blended_e31 = e31w / A if A else 0.0
    return dict(
        N=N, A=A, rate=(A / N if N else 0.0),
        avg_g=(gsum / A if A else 0.0),
        bad=bad, bad_rate=(bad / A if A else 0.0),
        loss=loss, pbt=pbt, rev=rev,
        pbt_pct=(pbt / rev if rev else 0.0),
        blended_e31=blended_e31,
    )


def score_optimal(df_seg: pd.DataFrame, PD, E31, ECON, thr: float | None = None) -> int:
    """Find the score threshold that maximises cumulative PBT in score mode.

    Sweeps every unique score value in the segment from high→low (approving
    progressively more accounts) and picks the threshold with the best PBT.
    When thr is given the blended Ever31@MOB3 of the approved pool must stay ≤ thr.
    Returns the optimal score threshold, or the segment max score (reject-all)
    if no profitable threshold exists.
    """
    if df_seg.empty:
        return 0
    scores = sorted(df_seg["score"].unique(), reverse=True)
    best_score = int(df_seg["score"].max())
    best_pbt   = float("-inf")
    for s in scores:
        stt = seg_stats(df_seg, s, "score", PD, E31, ECON)
        if stt["a"] == 0:
            continue
        if thr is not None and stt["blended_e31"] > thr:
            continue
        if stt["pbt"] > best_pbt:
            best_pbt  = stt["pbt"]
            best_score = s
    return best_score if best_pbt > 0 else int(df_seg["score"].max())


def apply_column_mapping(df: pd.DataFrame, score_col: str, grade_col: str, seg_col: str,
                          prod_col: str, grade_bands: list, bands_df: pd.DataFrame,
                          default_prod: str) -> pd.DataFrame:
    """Derive score/grade/segment/product columns onto df from the sidebar's column mapping.

    Mutates df in place (and returns it) — mirrors the mapping choices made in
    sidebar.render_column_mapping() against the Score→Grade bands from
    sidebar.render_assumptions().
    """
    if score_col != "(none)" and score_col in df.columns:
        df["score"] = pd.to_numeric(df[score_col], errors="coerce").fillna(0)
    elif "score" not in df.columns:
        df["score"] = 0

    if grade_col != "(derive from score)" and grade_col in df.columns:
        df["grade"] = pd.to_numeric(df[grade_col], errors="coerce")
    elif "grade" not in df.columns:
        fallback = grade_bands[len(grade_bands) // 2] if grade_bands else 5
        if len(bands_df) > 0:
            grade_out = pd.Series(pd.NA, index=df.index, dtype="Int64")
            for _, brow in bands_df.iterrows():
                m = (df["score"] >= brow["score_min"]) & (df["score"] <= brow["score_max"])
                grade_out[m] = int(brow["grade"])
            df["grade"] = grade_out.fillna(fallback).astype(int)
        else:
            df["grade"] = fallback

    if seg_col != "(none — one group)" and seg_col in df.columns:
        df["segment"] = df[seg_col].astype(str).fillna("(unknown)")
    elif "segment" not in df.columns:
        df["segment"] = "(all)"

    if prod_col != "(none — use default economics)" and prod_col in df.columns:
        df["product"] = df[prod_col].astype(str).fillna(default_prod)
    elif "product" not in df.columns:
        df["product"] = default_prod

    return df


def aqi_reverse(AQI) -> dict:
    cum = AQI["cc"] * 3
    lgd = AQI["lgd"] / 100
    pd_r = AQI["pd"] / 100
    e91 = cum / lgd if lgd else 0.0
    e31 = e91 / pd_r if pd_r else 0.0
    mob3 = e31 * AQI["lc"]
    return dict(cum=cum, e91=e91, e31=e31, mob3=mob3)


def aqi_forward(obs, AQI) -> float:
    lc = AQI["lc"]
    if not lc:
        return 0.0
    e31 = obs / lc
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


def aqi_limited_grade_portfolio(df: pd.DataFrame, segments, thr: float, E31, grade_bands=None,
                                 E31_SEG=None) -> int:
    """aqi_limited_grade across a multi-segment dataframe, resolving E31 per segment."""
    if grade_bands is None:
        grade_bands = GRADE_BANDS
    counts = {seg: df[df["segment"] == seg]["grade"].value_counts() for seg in segments}
    capN = capE = 0.0
    lim = 0
    for g in grade_bands:
        for seg in segments:
            c = int(counts[seg].get(g, 0))
            E31_eff = resolve_e31(seg, E31, E31_SEG)
            capN += c; capE += c * e31_of(g, E31_eff)
        blended = capE / capN if capN else 0.0
        if blended <= thr:
            lim = g
    return lim
