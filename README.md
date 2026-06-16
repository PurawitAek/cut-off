# Credit Decisioning Workbench

An interactive Streamlit tool for setting personal & nano loan approval cutoffs —
by Grade or Score — that maximise expected profit while staying inside an Asset
Quality (AQI) risk limit. Built for LINE BK credit risk.

## Setup

Requires Python 3.9+.

```bash
cd cut-off
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. No data upload is required — it loads
`default_input.xlsx` (1,000 synthetic applicants) automatically. Upload your own
CSV/XLSX via the sidebar to replace it.

> `credit_workbench.py` is an earlier, single-file prototype kept for reference.
> `app.py` (with the `tabs/` package) is the maintained entry point.

## What it does

Each applicant maps to a Grade (1 = best … 10 = worst). The core decision is a
grade-ordered cutoff: **approve grades 1..k, decline the rest**. The workbench
walks down the grades, accumulates marginal + cumulative volume/bad-rate/profit,
and recommends the cutoff **k\*** that maximises Profit Before Tax (PBT) — optionally
constrained to never breach an AQI-derived risk ceiling.

## Project structure

```
cut-off/
├── app.py                  # Entry point — data load, sidebar assumptions, filters,
│                            # cutoff mode, section router. Run this with streamlit.
├── ui.py                   # LINE BK header/theme (CSS injection) — visual only.
├── config.py               # Default assumptions (PD, E31, Economics, AQI, grade
│                            # bands) and shared color/format constants.
├── core.py                 # Pure calculation layer — no Streamlit import, fully
│                            # unit-testable. Every KPI formula lives here.
├── data.py                 # make_sample_data() — the built-in synthetic population.
├── make_default_xlsx.py    # Regenerates default_input.xlsx from config.py + data.py.
├── default_input.xlsx      # Shipped default dataset (applicants + reference tables).
├── requirements.txt
├── .streamlit/config.toml  # Native Streamlit theme colors.
├── assets/                  # LINE BK logo (header + favicon).
└── tabs/
    ├── explore.py           # Explore — score distribution, drill-down, pivot.
    ├── cutoff.py            # Cutoff & KPIs — per-segment sliders, grade walk table.
    ├── economics.py         # Economics — P&L waterfall, stress test, break-even.
    ├── aqi.py                # Asset Quality (AQI) — reverse/forward chain.
    ├── risk.py               # Concentration & PSI — HHI limits, population drift.
    ├── analytics.py          # Analytics — Overview/Grade/Segments/Bad Rate dashboard.
    └── simulator.py          # Simulator — what-if, champion vs challenger, frontier,
                               # data comparison.
```

## Input data

The app is tolerant of schema: only `score`, `grade`, `segment`, and `product`
matter for the calculations (auto-mapped by column name, remappable in the
sidebar), and any extra column automatically becomes a filter in the Explore tab.

| Path | What | Where to edit |
|---|---|---|
| 1 — Applicant data | CSV/XLSX upload, or the built-in default | Sidebar file uploader |
| 2 — Economics per product | Avg loan, EIR, COF, OPEX/CAC, LGD | Sidebar → "Economics per product" |
| 3 — Grade reference curves | Grade→PD(%), Grade→%Ever31@MOB3, Score→Grade bands | Sidebar expanders |

Any segment can override the global PD curve, E31 curve, or Economics via the
sidebar "Segment overrides" expander.

## Core calculations (summary)

```
Revenue per account        = Loan × EIR − Loan × COF
Expected Credit Loss (acct) = Loan × PD(grade) × LGD
PBT per account             = Revenue − OPEX − Expected Credit Loss
```

Walking grades 1→10, **Cumulative PBT** peaks at **k\*** — the profit-optimal
cutoff. The **AQI-limited grade** is the loosest cutoff whose blended
%Ever31@MOB3 still sits under the AQI threshold. The recommended cutoff is
`k*` alone ("Profit k\*") or `min(k*, AQI-limited grade)` ("Profit ∧ AQI").

All formulas — including the AQI reverse/forward chain, HHI, PSI, and every
tab's KPIs — are documented with exact source functions in
`Credit_Decisioning_Workbench_Guide.docx` (Sections 5–6) and in `core.py`
directly, which has no Streamlit dependency and can be imported/tested on its own.

## Workflow

1. Load data (default, or upload your own).
2. Map columns (Score / Grade / Segment / Product) if auto-detection is wrong.
3. Review/edit assumptions in the sidebar.
4. Apply global filters to scope the population.
5. Choose Cutoff mode (Grade/Score) and Optimize target (Profit k\* / Profit ∧ AQI).
6. Set cutoffs manually, or click **"Apply optimal to all"**.
7. Review across the seven tabs — each recomputes live from the same cutoffs.
8. Iterate, then export the filtered population as CSV from the Explore tab.

## Notes

- `.venv/` is gitignored — never commit it (see `.gitignore`).
- Re-run `python make_default_xlsx.py` after changing defaults in `config.py` to
  refresh the shipped `default_input.xlsx`.
