from __future__ import annotations
import numpy as np
import pandas as pd


def make_sample_data(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    segs = {
        "PL-Salaried":     ("Personal Loan", 0.26, 3.4, 1.8),
        "PL-SelfEmployed": ("Personal Loan", 0.18, 4.6, 2.1),
        "PL-NewToCredit":  ("Personal Loan", 0.12, 5.6, 2.2),
        "Nano-Existing":   ("Nano Loan",     0.16, 5.2, 2.0),
        "Nano-MicroBiz":   ("Nano Loan",     0.16, 6.4, 2.1),
        "Nano-Informal":   ("Nano Loan",     0.12, 7.3, 1.9),
    }
    names = list(segs)
    w = np.array([segs[s][1] for s in names]); w = w / w.sum()
    top, bottom = 880, 320
    band = (top - bottom) / 9
    rows = []
    for i in range(n):
        s = names[rng.choice(len(names), p=w)]
        prod, _, gm, gsd = segs[s]
        g = int(np.clip(round(rng.normal(gm, gsd)), 1, 10))
        center = top - (g - 1) * (top - bottom) / 9
        score = int(np.clip(center + rng.normal(0, 12), center - band / 2, center + band / 2))
        rows.append((f"CUST{100000 + i}", prod, s, score, g))
    df = pd.DataFrame(rows, columns=["customerID", "product", "segment", "score", "grade"])
    return df.sample(frac=1, random_state=1).reset_index(drop=True)
