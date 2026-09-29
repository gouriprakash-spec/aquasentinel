"""Date-grouped comparison: does adding Penn's Landing near-shore labels help?

No-turbidity feature set = shipped model features minus turbidity (turbidity IV raw not local).
CV: StratifiedGroupKFold(5), groups = sample date (no day on both sides), repeated over 10 seeds.
Metrics: precision / recall / F1 on 'unsafe' (>=235), pooled out-of-fold.
"""
import numpy as np, pandas as pd, json
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import precision_score, recall_score, f1_score

F = ["water_temp_c","sp_conductance_uscm","dissolved_oxygen_mgl","ph","precip_mm","precip_prev_24h_mm"]
SEEDS = range(10)
ch = pd.read_csv("../data/aquasentinel_labels_master.csv", parse_dates=["date"])
ch = ch[ch.proxy_available == 1].copy() if "proxy_available" in ch else ch
ch["nearshore"] = 0
ns = pd.read_csv("pennslanding_nearshore_labels.csv", parse_dates=["date"]); ns["nearshore"] = 1
ns["station_name"] = "NearShore"

def rf(seed): return Pipeline([("i", SimpleImputer(strategy="median")),
    ("c", RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=seed))])

def m(y, p): return dict(precision=round(precision_score(y,p,zero_division=0),3),
    recall=round(recall_score(y,p,zero_division=0),3), f1=round(f1_score(y,p,zero_division=0),3))

def cv(df, feats, eval_masks):
    """Repeated grouped CV; returns mean metrics per eval subset."""
    res = {k: [] for k in eval_masks}
    X, y, g = df[feats].to_numpy(), df.unsafe.astype(int).to_numpy(), df.date.dt.strftime("%Y-%m-%d").to_numpy()
    for s in SEEDS:
        oof = np.empty(len(y), int)
        for tr, te in StratifiedGroupKFold(5, shuffle=True, random_state=s).split(X, y, g):
            oof[te] = rf(s).fit(X[tr], y[tr]).predict(X[te])
        for k, mask in eval_masks.items():
            res[k].append(m(y[mask], oof[mask]))
    return {k: {mm: round(np.mean([r[mm] for r in v]),3) for mm in v[0]} | {"n": int(eval_masks[k].sum()), "pos": int(df.unsafe[eval_masks[k]].sum())} for k, v in res.items()}

out = {}
# E1 channel only (BFB+NY), CV
out["E1_channel_only_cv"] = cv(ch, F, {"channel": np.ones(len(ch), bool)})
# E2 transfer: train on channel, test on near-shore
y = ns.unsafe.to_numpy(); preds = []
for s in SEEDS: preds.append(m(y, rf(s).fit(ch[F], ch.unsafe).predict(ns[F])))
out["E2_channel_model_on_nearshore"] = {k: round(np.mean([p[k] for p in preds]),3) for k in preds[0]} | {"n": len(ns), "pos": int(y.sum())}
# E3 near-shore only CV
out["E3_nearshore_only_cv"] = cv(ns, F, {"nearshore": np.ones(len(ns), bool)})
# E4 pooled BFB+NY+near-shore, with nearshore flag
pool = pd.concat([ch, ns], ignore_index=True)
out["E4_pooled_all_cv"] = cv(pool, F+["nearshore"], {"nearshore": (pool.nearshore==1).to_numpy(), "channel": (pool.nearshore==0).to_numpy()})
# E5 pooled BFB + near-shore (Navy Yard dropped)
p2 = pool[pool.station_name != "Navy Yard"].reset_index(drop=True)
out["E5_pooled_BFB_nearshore_cv"] = cv(p2, F+["nearshore"], {"nearshore": (p2.nearshore==1).to_numpy(), "BFB": (p2.nearshore==0).to_numpy()})
# Rules fallback (prev-48h rain >= 2.5 mm, from app/config.py) on each set
for name, d in [("nearshore", ns), ("channel", ch)]:
    out[f"RULE_rain48_ge_2.5_{name}"] = m(d.unsafe, (d.precip_prev_48h_mm >= 2.5).astype(int)) | {"n": len(d), "pos": int(d.unsafe.sum())}
# Sensitivity: near-shore without the 2024 max-over-7-sites days
ns2 = ns[ns.date.dt.year != 2024].reset_index(drop=True)
out["S1_nearshore_only_cv_no2024"] = cv(ns2, F, {"nearshore": np.ones(len(ns2), bool)})
print(json.dumps(out, indent=1))
json.dump(out, open("comparison_results.json", "w"), indent=1)
