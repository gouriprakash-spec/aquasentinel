# Penn's Landing near-shore experiment (2026-09-27)

Question: do DRBC near-shore E. coli samples at Penn's Landing pair better with USGS 01467200 than the
Ben Franklin Bridge / Navy Yard channel stations?

## Files
- `build_nearshore_labels.py` -> `pennslanding_nearshore_labels.csv`: DRBC-DEL-LL + DRBC-6107-049..055 +
  DRBC-C1..C5 collapsed to one label per date (max). 133 raw samples -> 69 label-days, 30 unsafe
  (2019: 18/12, 2020: 17/2, 2021: 20/8, 2022: 8/2, 2024: 6/6). Joined to gauge daily proxies + NCEI precip
  using build_dataset.py loaders. No turbidity (usgs_iv_turbidity_raw.csv not in raw/).
- `compare_models.py` -> `comparison_results.json`.

## Method
Features = shipped model features minus turbidity: temp, sp. conductance, DO, pH, precip_mm, precip_prev_24h_mm.
RF as in app/model/train.py. StratifiedGroupKFold(5) grouped by DATE (no day in both train and test),
averaged over 10 seeds. Metrics on unsafe (>=235).

## Results (precision / recall / F1)
| Setup | Eval on | P | R | F1 |
|---|---|---|---|---|
| E1 channel only (BFB+NY) | channel (310 / 45 pos) | 0.15 | 0.05 | 0.08 |
| E2 channel model, applied to near-shore | near-shore (69 / 30) | 0.71 | 0.26 | 0.38 |
| E3 near-shore only | near-shore | 0.61 | 0.50 | 0.55 |
| E4 pooled channel + near-shore (+ flag) | near-shore | 0.72 | 0.52 | 0.60 |
| E5 pooled BFB + near-shore (NY dropped) | near-shore | 0.71 | 0.53 | 0.61 |
| Rule: prev-48h rain >= 2.5 mm | near-shore | 0.69 | 0.60 | 0.64 |
| Rule: prev-48h rain >= 2.5 mm | channel | 0.36 | 0.62 | 0.46 |
| Rule: prev-24h rain >= 2.5 mm | near-shore | 0.86 | 0.40 | - |

Sensitivity: dropping the 2024 (max-over-7-sites) days -> near-shore-only F1 0.58 (stable).
Adding precip_prev_48h_mm to the RF: near-shore-only F1 0.60, pooled 0.60 (no material change).

## Read
- Near-shore labels track the gauge + rain far better than channel labels (F1 ~0.6 vs ~0.1 without turbidity).
- Pooling channel + near-shore is the best ML variant for near-shore (precision up, recall same).
- A simple rain rule still matches or beats the RF on near-shore without turbidity.

## Turbidity-era follow-up (compare_turbidity.py -> comparison_turbidity_results.json)
Restored raw/usgs_iv_turbidity_raw.csv (USGS NWIS IV, param 63680, 2021-10-28 to 2026-09-05, 483k readings).
Daily mean/max reproduce regime_B_post2021.csv turbidity exactly (60/60 rows, max diff 0.0).
Shipped feature set (incl. turbidity). 14 near-shore turbidity-era days, 8 unsafe.

| Setup | Eval on | P | R | F1 |
|---|---|---|---|---|
| Shipped setup, ungrouped CV (as in train.py) | channel (60 / 15) | 0.54 | 0.46 | 0.49 |
| Shipped setup, DATE-grouped CV | channel | 0.19 | 0.09 | 0.12 |
| Shipped model applied to near-shore | near-shore (14 / 8) | 0.85 | 0.63 | 0.72 |
| Pooled regime B + near-shore, date-grouped | channel | 0.37 | 0.19 | 0.25 |
| Pooled regime B + near-shore, date-grouped | near-shore | 0.90 | 0.75 | 0.82 |
| Rule prev-48h >= 2.5 mm | channel | 0.55 | 0.73 | 0.63 |
| Rule prev-24h >= 2.5 mm | channel | 0.50 | 0.40 | 0.44 |
| Rule prev-24h (= 48h) >= 2.5 mm | near-shore | 1.00 | 0.63 | 0.77 |

KEY FINDING - leakage in the shipped metrics: regime B is 30 dates x 2 stations. BFB and Navy Yard share
the same gauge features on the same day, so StratifiedKFold puts identical feature rows in train and test.
train.py's reported 0.615 / 0.533 does not survive date grouping (0.19 / 0.09).
Turbidity does not separate near-shore labels (mean 6.0 FNU unsafe vs 6.9 safe); it does for channel (10.0 vs 6.9).
Near-shore n=14 is too small to trust the 0.82 F1; treat as directional.
Also found: USGS series catalog shows DAILY turbidity at 01467200 for 2008-09-10 to 2011-12-12 (older sensor) -
a possible (instrument-change-caveated) way to add turbidity-era channel days.
