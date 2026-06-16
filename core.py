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
