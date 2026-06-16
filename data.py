from __future__ import annotations
import numpy as np
import pandas as pd

# Segment parameters derived from mock_scores_smooth.csv
# (mean, std, nano_pct, weight)  — score range 300-900, 10 equal-width grade bands
_SEGS = {
    #  segment          product    score_mean  score_std  nano_pct  weight
    "Salaried":     ("Personal Loan",  708,  78, 0.247, 0.233),
    "SelfEmployed": ("Personal Loan",  629,  79, 0.456, 0.210),
    "Existing":     ("Nano Loan",      676,  82, 0.475, 0.161),
    "MicroBiz":     ("Nano Loan",      572,  84, 0.710, 0.166),
    "NewToCredit":  ("Personal Loan",  599,  81, 0.413, 0.118),
    "Informal":     ("Nano Loan",      519,  80, 0.744, 0.112),
}

# Grade bands: grade 1 (best) = highest scores, grade 10 (worst) = lowest scores
_SCORE_MIN = 300
_SCORE_MAX = 900
_N_GRADES  = 10
_BAND_W    = (_SCORE_MAX - _SCORE_MIN) / _N_GRADES  # 60 pts per band


def _score_to_grade(score: int) -> int:
    """Map score 300–900 → grade 1 (best) … 10 (worst)."""
    g = int((_SCORE_MAX - score) / _BAND_W) + 1
    return int(np.clip(g, 1, _N_GRADES))


def make_sample_data(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    seg_names = list(_SEGS)
    weights = np.array([_SEGS[s][4] for s in seg_names])
    weights = weights / weights.sum()

    rows = []
    for i in range(n):
        seg = seg_names[rng.choice(len(seg_names), p=weights)]
        default_prod, mean, std, nano_pct, _ = _SEGS[seg]
        prod = "Nano Loan" if rng.random() < nano_pct else "Personal Loan"
        score = int(np.clip(round(rng.normal(mean, std)), _SCORE_MIN, _SCORE_MAX))
        grade = _score_to_grade(score)
        rows.append((f"APP{200000 + i}", prod, seg, score, grade))

    df = pd.DataFrame(rows, columns=["id", "product", "segment", "score", "grade"])
    return df.sample(frac=1, random_state=1).reset_index(drop=True)
