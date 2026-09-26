#!/usr/bin/env python3
"""
AquaSentinel — training dataset builder (tidal Delaware proof-of-concept).

Pairs USGS Penn's Landing (01467200) water-quality proxies + NCEI Philadelphia
airport rainfall with DRBC E. coli grab-sample labels at Ben Franklin Bridge
(892071) and Navy Yard (892065), then labels each sample against the EPA
single-sample recreational threshold (235 CFU/100 mL) and splits the result
into two regimes that must be validated SEPARATELY:

  Regime A (pre-2021): temp / specific conductance / DO / pH / rainfall.
                       NO turbidity (continuous turbidity begins 2021-10-28).
  Regime B (post-2021): the above PLUS turbidity (daily mean & max).

Run `python build_dataset.py --fetch` to re-pull all raw sources from the
public APIs; run with no flag to build from the raw CSVs already in ./raw.

Sources
  - USGS NWIS Water Data (waterservices.usgs.gov) via `dataretrieval`
  - EPA Water Quality Portal (waterqualitydata.us)
  - NOAA NCEI daily summaries (ncei.noaa.gov), station USW00013739 (PHL airport)

NOTE: this builds a proof-of-concept training set. Every value is real,
pulled from the public record; nothing is synthesized. See DATA-DICTIONARY.md.
"""
import argparse, io, os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

RAW = os.path.join(os.path.dirname(__file__), "raw")
OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(RAW, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

USGS_SITE = "01467200"                      # Delaware R at Penn's Landing, Philadelphia
WQP_SITES = ["31DELRBC_WQX-892071",         # Ben Franklin Bridge
             "31DELRBC_WQX-892065"]         # Navy Yard
NCEI_STN = "USW00013739"                     # Philadelphia Intl Airport
TURBIDITY_START = "2021-10-28"               # first continuous turbidity date
EPA_SINGLE_SAMPLE = 235.0                    # CFU/100 mL, single-sample max
EPA_GEOMEAN = 126.0                          # CFU/100 mL, 30-day geometric mean

STATION_NAMES = {"31DELRBC_WQX-892071": "Ben Franklin Bridge",
                 "31DELRBC_WQX-892065": "Navy Yard"}


# ----------------------------------------------------------------------------- fetch
def fetch():
    import requests
    from dataretrieval import nwis
    print("Fetching WQP E. coli labels ...")
    params = [("mimeType", "csv"), ("zip", "no"),
              ("characteristicName", "Escherichia coli")]
    for s in WQP_SITES:
        params.append(("siteid", s))
    r = requests.get("https://www.waterqualitydata.us/data/Result/search",
                     params=params, timeout=180)
    open(os.path.join(RAW, "wqp_ecoli_raw.csv"), "wb").write(r.content)

    print("Fetching USGS daily proxies ...")
    dv, _ = nwis.get_dv(sites=USGS_SITE,
                        parameterCd=["00010", "00095", "00300", "00400"],
                        start="2005-01-01", end="2026-09-05")
    dv.to_csv(os.path.join(RAW, "usgs_dv_raw.csv"))

    print("Fetching USGS instantaneous turbidity ...")
    iv, _ = nwis.get_iv(sites=USGS_SITE, parameterCd="63680",
                        start=TURBIDITY_START, end="2026-09-05")
    iv.to_csv(os.path.join(RAW, "usgs_iv_turbidity_raw.csv"))

    print("Fetching NCEI rainfall ...")
    u = ("https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries"
         f"&stations={NCEI_STN}&startDate=2005-01-01&endDate=2025-12-31"
         "&dataTypes=PRCP&format=csv&units=metric")
    r = requests.get(u, timeout=120)
    open(os.path.join(RAW, "ncei_precip_raw.csv"), "wb").write(r.content)
    print("Done fetching.")


# ------------------------------------------------------------------- helpers / clean
def _coalesce(df, candidates):
    """Return first-non-null across candidate columns (primary sensor first)."""
    out = pd.Series(np.nan, index=df.index)
    for c in candidates:
        if c in df.columns:
            out = out.fillna(pd.to_numeric(df[c], errors="coerce"))
    return out


def load_proxies():
    dv = pd.read_csv(os.path.join(RAW, "usgs_dv_raw.csv"), dtype=str)
    dv["date"] = pd.to_datetime(dv["datetime"], errors="coerce").dt.date
    # Coalesce the primary series with the secondary "barge" monitor series.
    prox = pd.DataFrame({"date": dv["date"]})
    prox["water_temp_c"] = _coalesce(dv, ["00010_Mean",
                                          "00010_ism test bed, [ism barge_Mean"])
    prox["sp_conductance_uscm"] = _coalesce(dv, ["00095_Mean",
                                          "00095_ism test bed, [ism barge_Mean"])
    prox["dissolved_oxygen_mgl"] = _coalesce(dv, ["00300_Mean",
                                          "00300_ism test bed, [ism barge_Mean"])
    prox["ph"] = _coalesce(dv, ["00400_Median",
                                "00400_ism test bed, [ism barge_Median"])
    prox = prox.groupby("date", as_index=False).mean(numeric_only=True)

    # Turbidity from instantaneous -> daily mean & max.
    # NOTE: usgs_iv_turbidity_raw.csv is large and is not always kept in raw/.
    # If it is absent, degrade gracefully (turbidity left blank) so an offline
    # rebuild still reproduces every non-turbidity column; run --fetch to restore it.
    turb_path = os.path.join(RAW, "usgs_iv_turbidity_raw.csv")
    if os.path.exists(turb_path):
        iv = pd.read_csv(turb_path, dtype=str)
        tcol = [c for c in iv.columns if c.startswith("63680") and not c.endswith("_cd")]
        iv["date"] = pd.to_datetime(iv["datetime"], errors="coerce", utc=True).dt.tz_convert(
            "America/New_York").dt.date
        iv["turb"] = pd.to_numeric(iv[tcol[0]], errors="coerce")
        turb = iv.groupby("date").agg(turbidity_fnu_mean=("turb", "mean"),
                                      turbidity_fnu_max=("turb", "max")).reset_index()
        prox = prox.merge(turb, on="date", how="left")
    else:
        warnings.warn("usgs_iv_turbidity_raw.csv not found in raw/ -- turbidity "
                      "columns will be blank. Run `build_dataset.py --fetch` to restore.")
        prox["turbidity_fnu_mean"] = np.nan
        prox["turbidity_fnu_max"] = np.nan
    prox["date"] = pd.to_datetime(prox["date"])
    return prox


def load_precip():
    p = pd.read_csv(os.path.join(RAW, "ncei_precip_raw.csv"))
    p["date"] = pd.to_datetime(p["DATE"], errors="coerce")
    p = p[["date", "PRCP"]].rename(columns={"PRCP": "precip_mm"})
    p["precip_mm"] = pd.to_numeric(p["precip_mm"], errors="coerce").fillna(0.0)
    p = p.sort_values("date").reset_index(drop=True)
    # antecedent windows (shifted so "prior" excludes the sample day itself)
    p["precip_prev_24h_mm"] = p["precip_mm"].shift(1)
    p["precip_prev_48h_mm"] = p["precip_mm"].shift(1).rolling(2).sum()
    p["precip_prev_72h_mm"] = p["precip_mm"].shift(1).rolling(3).sum()
    p["precip_prev_7d_mm"] = p["precip_mm"].shift(1).rolling(7).sum()
    p["rain_prev_48h_flag"] = (p["precip_prev_48h_mm"] > 2.5).astype(int)  # >0.1 in
    return p


def load_labels():
    df = pd.read_csv(os.path.join(RAW, "wqp_ecoli_raw.csv"), dtype=str, low_memory=False)
    df = df[df["CharacteristicName"] == "Escherichia coli"].copy()
    df["date"] = pd.to_datetime(df["ActivityStartDate"], errors="coerce")
    df["station_id"] = df["MonitoringLocationIdentifier"]
    df["station_name"] = df["station_id"].map(STATION_NAMES)

    raw_val = df["ResultMeasureValue"].astype(str).str.strip()
    df["value_censored"] = raw_val.str.contains(r"[<>]", na=False) | \
        df["ResultDetectionConditionText"].notna()
    num = pd.to_numeric(raw_val.str.replace(r"[<>=]", "", regex=True),
                        errors="coerce")
    # Non-detect with no number -> treat as 1 CFU (clearly below threshold, safe)
    nd = df["ResultDetectionConditionText"].notna() & num.isna()
    num = num.where(~nd, 1.0)
    df["ecoli_cfu_100ml"] = num
    df["unit"] = df["ResultMeasure/MeasureUnitCode"]
    df = df.dropna(subset=["date", "ecoli_cfu_100ml"])

    # Multiple results at same station+date -> keep the max (conservative for safety)
    df = (df.sort_values("ecoli_cfu_100ml")
            .groupby(["station_id", "date"], as_index=False)
            .agg(station_name=("station_name", "first"),
                 ecoli_cfu_100ml=("ecoli_cfu_100ml", "max"),
                 value_censored=("value_censored", "max"),
                 unit=("unit", "first")))
    return df


# --------------------------------------------------------------------------- assemble
def build():
    labels = load_labels()
    prox = load_proxies()
    precip = load_precip()

    df = labels.merge(prox, on="date", how="left").merge(precip, on="date", how="left")

    # Target
    df["unsafe"] = (df["ecoli_cfu_100ml"] >= EPA_SINGLE_SAMPLE).astype(int)
    df["exceeds_geomean_126"] = (df["ecoli_cfu_100ml"] >= EPA_GEOMEAN).astype(int)

    # Regime assignment
    tstart = pd.Timestamp(TURBIDITY_START)
    df["regime"] = np.where(df["date"] >= tstart, "B_post2021", "A_pre2021")
    df["turbidity_available"] = (df["date"] >= tstart).astype(int)

    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    df["recreation_season"] = df["month"].between(5, 9).astype(int)  # May-Sep

    ident = ["station_id", "station_name", "date", "year", "month",
             "recreation_season", "regime"]
    proxy_cols = ["water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl", "ph"]
    # proxy_available = row carries at least one USGS proxy reading, i.e. it is
    # usable by a proxy model. Rows with every proxy null (an E. coli label + rain
    # but no gauge reading that day) are kept in the master but held out of the
    # regime training files. This is what makes master(330) != regimeA + regimeB.
    df["proxy_available"] = df[proxy_cols].notna().any(axis=1).astype(int)
    turb_cols = ["turbidity_fnu_mean", "turbidity_fnu_max"]
    precip_cols = ["precip_mm", "precip_prev_24h_mm", "precip_prev_48h_mm",
                   "precip_prev_72h_mm", "precip_prev_7d_mm", "rain_prev_48h_flag"]
    label_cols = ["ecoli_cfu_100ml", "value_censored", "unit",
                  "unsafe", "exceeds_geomean_126"]

    master = df[ident + proxy_cols + turb_cols + precip_cols + label_cols
                + ["proxy_available"]] \
        .sort_values(["date", "station_id"]).reset_index(drop=True)
    master.to_csv(os.path.join(OUT, "aquasentinel_labels_master.csv"), index=False)

    # A regime training row must carry >=1 proxy reading (drop all-proxy-null rows).
    # The SAME guard is applied to both regimes -- keeping it on A only was a bug
    # that let 4 feature-less rows sit in regime B.
    # Regime A (pre-2021): drop turbidity columns entirely
    a_feats = proxy_cols + precip_cols
    A = master[master["regime"] == "A_pre2021"][ident + a_feats + label_cols].copy()
    A = A.dropna(subset=proxy_cols, how="all")
    A.to_csv(os.path.join(OUT, "regime_A_pre2021.csv"), index=False)

    # Regime B (post-2021): include turbidity
    b_feats = proxy_cols + turb_cols + precip_cols
    B = master[master["regime"] == "B_post2021"][ident + b_feats + label_cols].copy()
    B = B.dropna(subset=proxy_cols, how="all")
    B.to_csv(os.path.join(OUT, "regime_B_post2021.csv"), index=False)

    # -------- report
    def summarize(name, d, feats):
        n = len(d)
        pos = int(d["unsafe"].sum())
        comp = int(d[feats].notna().all(axis=1).sum()) if n else 0
        print(f"\n{name}: {n} rows | unsafe={pos} ({pos/n*100:.0f}%) "
              f"| complete-feature rows={comp} | {d['year'].min()}-{d['year'].max()}"
              if n else f"\n{name}: 0 rows")

    print("=" * 64)
    print("AquaSentinel dataset build complete")
    print("=" * 64)
    summarize("MASTER (all)", master, proxy_cols)
    summarize("Regime A pre-2021", A, proxy_cols)
    summarize("Regime B post-2021", B, proxy_cols + turb_cols)
    print("\nRegime B turbidity coverage: "
          f"{int(B['turbidity_fnu_mean'].notna().sum())}/{len(B)} rows have turbidity")
    print(f"\nWrote to {OUT}/:")
    for f in ["aquasentinel_labels_master.csv", "regime_A_pre2021.csv",
              "regime_B_post2021.csv"]:
        print("  -", f)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="re-pull raw sources")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    build()
