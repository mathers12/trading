"""Trénovanie filtra setupov (strojové učenie) s poctivým overením.

1. Zozbierajú sa VŠETKY setupy s voľnými pravidlami (veľa obchodov).
2. Model (gradient boosting) sa naučí na staršom období (IS), ktoré setupy končia ziskom.
3. Prah pravdepodobnosti sa vyberie iba z IS (out-of-fold predikcie).
4. Výsledok sa meria na novšom období (OOS), ktoré model nikdy nevidel.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from . import engine, report
from .config import apply_overrides
from .research import metrics

LOOSE = [
    "bias.filter=false",
    "filters.signal_windows_local=[\"08:00-19:00\"]",
    "filters.signal_windows_ny=[]",
    "filters.sweep_kinds=null",
    "filters.require_h4_crt=false",
    "risk.one_position_at_a_time=false",
    "risk.max_trades_per_day=99",
    "risk.max_losses_per_day=99",
]
CATEGORICAL = ["direction", "sweep_kind", "entry_type", "bias_source", "session", "target_kind"]
BOOLEAN = ["bias_aligned", "w1_aligned", "target_is_bias_dol", "strong_body", "h4_crt", "inducement",
           "premium_discount", "midnight_open", "judas", "smt"]
NUMERIC = ["hour_ny", "sweep_depth_pips", "mss_bars", "sl_pips", "liq_rr", "weekday_n"]


def collect(ctx, cfg: dict) -> pd.DataFrame:
    ctx.cfg = apply_overrides(cfg, LOOSE)
    t = report.to_frame(engine.run(ctx)["setups"])
    t = t[t["status"] == "FILLED"].dropna(subset=["r"]).copy()
    t["weekday_n"] = t["signal_time"].dt.tz_convert("America/New_York").dt.weekday
    return t.sort_values("signal_time").reset_index(drop=True)


def features(t: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    x = t[NUMERIC].astype(float).copy()
    for c in BOOLEAN:
        x[c] = t[c].astype(bool).astype(float)
    x["htf_poi"] = t["htf_poi"].fillna("").astype(bool).astype(float)
    x["vp"] = t["vp"].fillna("").astype(bool).astype(float)
    x = pd.concat([x, pd.get_dummies(t[CATEGORICAL].astype(str), dtype=float)], axis=1)
    if columns is not None:
        x = x.reindex(columns=columns, fill_value=0.0)
    return x


def _model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=40,
                                          l2_regularization=1.0, random_state=0)


def oof_proba(x: pd.DataFrame, y: np.ndarray, folds: int = 4) -> np.ndarray:
    """Predikcie pre IS vždy z modelu, ktorý daný úsek nevidel (časové bloky)."""
    p = np.full(len(y), np.nan)
    edges = np.linspace(0, len(y), folds + 1).astype(int)
    for k in range(1, folds):  # prvý blok iba na trénovanie
        tr, te = slice(0, edges[k]), slice(edges[k], edges[k + 1])
        m = _model().fit(x.iloc[tr], y[tr])
        p[te] = m.predict_proba(x.iloc[te])[:, 1]
    return p


def run(ctx, cfg: dict, split: str) -> dict:
    t = collect(ctx, cfg)
    split_ts = pd.Timestamp(split, tz="UTC")
    is_mask = (t["signal_time"] < split_ts).to_numpy()
    x = features(t)
    y = (t["r"] > 0).to_numpy().astype(int)
    r = t["r"].to_numpy()
    days_is = max(np.busday_count(t["signal_time"].iloc[0].date(), split_ts.date()), 1)
    days_oos = max(np.busday_count(split_ts.date(), t["signal_time"].iloc[-1].date()), 1)

    x_is, y_is, r_is = x[is_mask], y[is_mask], r[is_mask]
    p_oof = oof_proba(x_is, y_is)
    model = _model().fit(x_is, y_is)
    p_oos = model.predict_proba(x[~is_mask])[:, 1]
    r_oos = r[~is_mask]

    rows = []
    valid = ~np.isnan(p_oof)
    for q in (0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9):
        thr = float(np.quantile(p_oof[valid], q)) if q > 0 else 0.0
        a = metrics(r_is[valid][p_oof[valid] >= thr])
        b = metrics(r_oos[p_oos >= thr])
        rows.append({"prah (kvantil IS)": q, "prah": round(thr, 3),
                     "IS_n/deň": round(a["n"] / (days_is * valid.mean()), 2), "IS_win%": a["win%"], "IS_PF": a["PF"], "IS_avgR": a["avgR"],
                     "OOS_n/deň": round(b["n"] / days_oos, 2), "OOS_win%": b["win%"], "OOS_PF": b["PF"], "OOS_avgR": b["avgR"],
                     "OOS_DD": b["DD"]})
    return {"table": pd.DataFrame(rows), "n_is": int(is_mask.sum()), "n_oos": int((~is_mask).sum()), "trades": t}
