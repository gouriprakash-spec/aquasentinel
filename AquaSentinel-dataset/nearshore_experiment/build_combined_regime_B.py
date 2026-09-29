"""Write data/regime_B_plus_nearshore.csv: regime B (BFB + Navy Yard, turbidity era) plus the
Penn's Landing near-shore turbidity-era label-days. Existing CSVs are read, never modified.
Run build_nearshore_labels.py first. Split any CV by `date` (stations share dates)."""
import os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(os.path.dirname(HERE), "data")
B = pd.read_csv(os.path.join(DATA, "regime_B_post2021.csv"), parse_dates=["date"])
B["nearshore"] = 0; B["source_sites"] = B["station_id"]
ns = pd.read_csv(os.path.join(HERE, "pennslanding_nearshore_labels.csv"), parse_dates=["date"])
ns = ns[ns.turbidity_fnu_mean.notna()].copy()
ns = ns.assign(station_id="PENNSLANDING-NEARSHORE", station_name="Penns Landing near-shore (collapsed)",
    year=ns.date.dt.year, month=ns.date.dt.month, regime="B_post2021",
    recreation_season=ns.date.dt.month.between(5, 9).astype(int),
    ecoli_cfu_100ml=ns.ecoli_mpn_100ml, value_censored=ns.censored.astype(bool), unit="MPN/100mL",
    exceeds_geomean_126=(ns.ecoli_mpn_100ml >= 126).astype(int), nearshore=1, source_sites=ns.sites)
cols = list(B.columns)
out = pd.concat([B, ns[cols]], ignore_index=True).sort_values(["date", "station_id"])
out["date"] = out.date.dt.strftime("%Y-%m-%d")
p = os.path.join(DATA, "regime_B_plus_nearshore.csv"); out.to_csv(p, index=False)
print(p, out.shape, "unsafe", int(out.unsafe.sum()), "dates", out.date.nunique())
print(out.groupby("station_name").agg(rows=("unsafe","size"), unsafe=("unsafe","sum")))
print("nulls:", out.isna().sum()[out.isna().sum()>0].to_dict())
