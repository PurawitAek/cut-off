from __future__ import annotations

PD_DEFAULT = [2.2407, 4.1279, 4.8720, 6.8772, 13.5296, 15.3127, 22.8240, 14.6083, 7.6302, 7.9774]
E31_DEFAULT = [0.05, 0.10, 0.20, 0.40, 0.70, 1.05, 1.60, 2.40, 3.50, 5.00]
ECON_DEFAULT = {
    "Personal Loan": dict(loan=30000, eir=0.2203, cof=0.015, opex=3312, lgd=0.865),
    "Nano Loan":     dict(loan=10000,  eir=0.33,  cof=0.04,  opex=3000,  lgd=0.865),
}
AQI_DEFAULT = dict(cc=16.38, lgd=75.0, pd=86.5, lc=0.0328)

GRADE_BANDS = list(range(1, 11))

TEAL = "#0E7C86"
GOOD = "#2F9E6E"
MID  = "#E8A33D"
BAD  = "#D9483B"
INK  = "#16202C"
MUTE = "#5C6B7A"


def thb(v: float) -> str:
    return "฿{:,.0f}".format(v)
