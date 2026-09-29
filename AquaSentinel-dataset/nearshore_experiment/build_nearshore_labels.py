"""Build a collapsed Penn's Landing near-shore label set (one label per date).

Sources (drbc_nearshore_ecoli_2019_2025.csv, DRBC via WQP, MPN/100mL):
  - DRBC-DEL-LL   Penns Landing Lagoon (2019-2022)
  - DRBC-6107-049..055 (2024 cluster within ~1 km of the lagoon; 6107-050 is ~45 m away)
  - DRBC-C1..C5   Penns Landing cross-section transect (2021)
Same-date samples across these sites are collapsed to the MAX (conservative, matches
build_dataset.py's same-station-same-date rule) so one rainy morning is one label.
Joined to USGS 01467200 daily proxies and NCEI PHL precip via build_dataset.py loaders.
Turbidity (daily mean/max) joined when raw/usgs_iv_turbidity_raw.csv is present (restored 2026-09-27).
"""
import os, sys, warnings
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.dirname(HERE)
sys.path.insert(0, DS)
warnings.filterwarnings("ignore")
import build_dataset as bd

SRC = os.path.join(DS, "..", "..", "drbc_nearshore_ecoli_2019_2025.csv")
SITES = (["DRBC-DEL-LL"] + [f"DRBC-6107-0{n}" for n in range(49, 56)]
         + [f"DRBC-C{n}" for n in range(1, 6)])

def build():
    d = pd.read_csv(SRC, parse_dates=["date"])
    d = d[d.site_id.isin(SITES)]
    lab = (d.groupby("date")
             .agg(ecoli_mpn_100ml=("ecoli_value", "max"),
                  n_samples=("ecoli_value", "size"),
                  n_sites=("site_id", "nunique"),
                  sites=("site_id", lambda s: ";".join(sorted(set(s)))),
                  censored=("detection_condition", lambda s: s.notna().any()))
             .reset_index())
    lab["station_name"] = "Penns Landing near-shore (collapsed)"
    lab["unsafe"] = (lab.ecoli_mpn_100ml >= 235).astype(int)
    df = lab.merge(bd.load_proxies(), on="date", how="left").merge(bd.load_precip(), on="date", how="left")
    out = os.path.join(HERE, "pennslanding_nearshore_labels.csv")
    df.to_csv(out, index=False)
    print(f"{len(df)} label-days, {df.unsafe.sum()} unsafe; from {d.shape[0]} raw samples")
    print(df.groupby(df.date.dt.year).agg(days=("unsafe", "size"), unsafe=("unsafe", "sum")))
    print("turbidity-era days:", int(df.turbidity_fnu_mean.notna().sum()), "unsafe:", int(df.loc[df.turbidity_fnu_mean.notna(),"unsafe"].sum()))
    print("missing proxies:", df[["water_temp_c","sp_conductance_uscm","dissolved_oxygen_mgl","ph","precip_mm"]].isna().sum().to_dict())
    return df

if __name__ == "__main__":
    build()
