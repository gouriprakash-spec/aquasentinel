"""Turbidity-era (regime B) comparison: shipped feature set, with vs without Penn's Landing near-shore days.
CV grouped by DATE (StratifiedGroupKFold, 5 folds where possible), 10 seeds."""
import numpy as np, pandas as pd, json
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.metrics import precision_score, recall_score, f1_score
F = ["water_temp_c","sp_conductance_uscm","dissolved_oxygen_mgl","ph","precip_mm","precip_prev_24h_mm","turbidity_fnu_mean","turbidity_fnu_max"]
SEEDS = range(10)
B = pd.read_csv("../data/regime_B_post2021.csv", parse_dates=["date"]); B["nearshore"] = 0
ns = pd.read_csv("pennslanding_nearshore_labels.csv", parse_dates=["date"])
ns = ns[ns.turbidity_fnu_mean.notna()].copy(); ns["nearshore"] = 1; ns["station_name"] = "NearShore"
rf = lambda s: Pipeline([("i", SimpleImputer(strategy="median")), ("c", RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=s))])
m = lambda y, p: dict(precision=precision_score(y,p,zero_division=0), recall=recall_score(y,p,zero_division=0), f1=f1_score(y,p,zero_division=0))
def avg(L, extra): return {k: round(float(np.mean([d[k] for d in L])),3) for k in L[0]} | extra
def cv(df, feats, masks, grouped=True):
    X, y, g = df[feats].to_numpy(), df.unsafe.astype(int).to_numpy(), df.date.dt.strftime("%F").to_numpy()
    res = {k: [] for k in masks}
    for s in SEEDS:
        oof = np.empty(len(y), int)
        spl = StratifiedGroupKFold(5, shuffle=True, random_state=s).split(X, y, g) if grouped else StratifiedKFold(5, shuffle=True, random_state=s).split(X, y)
        for tr, te in spl: oof[te] = rf(s).fit(X[tr], y[tr]).predict(X[te])
        for k, mk in masks.items(): res[k].append(m(y[mk], oof[mk]))
    return {k: avg(v, {"n": int(masks[k].sum()), "pos": int(df.unsafe[masks[k]].sum())}) for k, v in res.items()}
out = {}
allB = np.ones(len(B), bool)
out["B0_shipped_setup_ungrouped_cv"] = cv(B, F, {"channel": allB}, grouped=False)
out["B0_shipped_setup_date_grouped_cv"] = cv(B, F, {"channel": allB})
y = ns.unsafe.to_numpy()
out["B1_shipped_model_on_nearshore"] = avg([m(y, rf(s).fit(B[F], B.unsafe).predict(ns[F])) for s in SEEDS], {"n": len(ns), "pos": int(y.sum())})
pool = pd.concat([B, ns], ignore_index=True)
out["B2_pooled_B_plus_nearshore"] = cv(pool, F+["nearshore"], {"channel": (pool.nearshore==0).to_numpy(), "nearshore": (pool.nearshore==1).to_numpy(), "all": np.ones(len(pool), bool)})
for nm, d in [("nearshore", ns), ("channel", B)]:
    out[f"RULE_rain24_ge_2.5_{nm}"] = {k: round(v,3) for k, v in m(d.unsafe, (d.precip_prev_24h_mm >= 2.5).astype(int)).items()} | {"n": len(d)}
    out[f"RULE_rain48_ge_2.5_{nm}"] = {k: round(v,3) for k, v in m(d.unsafe, (d.precip_prev_48h_mm >= 2.5).astype(int)).items()} | {"n": len(d)}
out["nearshore_turb_mean_by_label"] = ns.groupby("unsafe").turbidity_fnu_mean.mean().round(1).to_dict()
out["channel_turb_mean_by_label"] = B.groupby("unsafe").turbidity_fnu_mean.mean().round(1).to_dict()
out["dates_shared_BFB_NY_in_B"] = int(B.groupby("date").size().gt(1).sum())
print(json.dumps(out, indent=1, default=str)); json.dump(out, open("comparison_turbidity_results.json","w"), indent=1, default=str)
