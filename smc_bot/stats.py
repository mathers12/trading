"""Štatistika backtestu v R (násobky rizika)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summary(trades: pd.DataFrame, risk_pct: float = 1.0) -> dict:
    t = trades[trades["status"] == "FILLED"].dropna(subset=["r"]) if len(trades) else trades
    n = len(t)
    if n == 0:
        return {"obchodov": 0}
    r = t["r"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    streak = best = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    months = max((t["signal_time"].max() - t["signal_time"].min()).days / 30.44, 1)
    return {
        "setupov": len(trades),
        "obchodov": n,
        "TP": int((t["result"] == "TP").sum()),
        "SL": int((t["result"] == "SL").sum()),
        "EOD": int((t["result"] == "EOD").sum()),
        "úspešnosť_%": round(100 * len(wins) / n, 1),
        "priemer_R": round(float(r.mean()), 3),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else float("inf"),
        "spolu_R": round(float(r.sum()), 2),
        "spolu_%_účtu": round(float(r.sum() * risk_pct), 1),
        "max_drawdown_R": round(float(dd.min()), 2),
        "najdlhšia_séria_strát": best,
        "obchodov_mesačne": round(n / months, 1),
        "priemerné_RR": round(float(t["rr"].mean()), 2),
        "nenaplnené": int((trades["status"] == "NOT_FILLED").sum()),
        "ušlo_(TP_pred_vstupom)": int((trades["status"] == "MISSED").sum()),
    }


def breakdown(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    t = trades[trades["status"] == "FILLED"].dropna(subset=["r"])
    if not len(t):
        return pd.DataFrame()
    key = t[column]
    if key.dtype == object and column in ("htf_poi", "vp"):
        key = key.fillna("").map(lambda v: "áno" if v else "nie")
    elif key.dtype == bool:
        key = key.map({True: "áno", False: "nie"})
    g = t.groupby(key)["r"]
    out = pd.DataFrame({
        "obchodov": g.size(),
        "úspešnosť_%": (g.apply(lambda s: (s > 0).mean() * 100)).round(1),
        "priemer_R": g.mean().round(3),
        "spolu_R": g.sum().round(2),
    })
    out.index.name = column
    return out


BREAKDOWNS = [
    ("session", "Seansa"),
    ("direction", "Smer"),
    ("weekday", "Deň v týždni"),
    ("month", "Mesiac"),
    ("sweep_kind", "Vybratá likvidita"),
    ("target_kind", "Cieľ (TP)"),
    ("entry_type", "Typ vstupu"),
    ("bias_source", "Zdroj biasu"),
    ("bias_aligned", "V smere biasu"),
    ("w1_aligned", "V smere W1 biasu"),
    ("target_is_bias_dol", "TP = likvidita daily biasu"),
    ("h4_crt", "H4 CRT"),
    ("htf_poi", "V HTF POI (OB/FVG)"),
    ("vp", "Volume profile (POC/VAH/VAL/nPOC)"),
    ("inducement", "Inducement"),
    ("premium_discount", "Premium/discount"),
    ("strong_body", "Silná displacement sviečka"),
]


def enrich(trades: pd.DataFrame) -> pd.DataFrame:
    if not len(trades):
        return trades
    t = trades.copy()
    ny = t["signal_time"].dt.tz_convert("America/New_York")
    days = ["Po", "Ut", "St", "Št", "Pi", "So", "Ne"]
    t["weekday"] = ny.dt.weekday.map(lambda k: f"{k + 1} {days[k]}")
    t["month"] = ny.dt.strftime("%Y-%m")
    return t
