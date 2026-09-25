"""Kolo učenia stratégie: test na viacerých mesiacoch + rozbor stratových obchodov.

python -m smc_bot.iterate --config config_htf.yaml --months 2025-03,2025-07,2025-08 --label "kolo 1" [--set k=v ...]
Výsledok kola sa pripíše do reports/iteracie.csv.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from . import engine
from .config import load_config
from .context import build_context
from .data import load_m1


def excursions(s: dict, m1a: dict) -> tuple[float, float]:
    """MFE a MAE v R od naplnenia po výstup (koľko šiel obchod v prospech / proti)."""
    o = m1a["open_ns"]
    a, b = np.searchsorted(o, s["fill_ns"]), np.searchsorted(o, s["exit_ns"])
    b = max(b, a + 1)
    long = s["direction"] == "LONG"
    risk = abs(s["entry"] - s["sl"])
    hi, lo = m1a["bid_high"][a:b].max(), m1a["bid_low"][a:b].min()
    fav = (hi - s["entry"]) if long else (s["entry"] - lo)
    adv = (s["entry"] - lo) if long else (hi - s["entry"])
    return round(fav / risk, 2), round(adv / risk, 2)


def run(cfg_path: str, months: list[str], overrides: list[str]) -> pd.DataFrame:
    cfg = load_config(cfg_path, overrides)
    rows = []
    for m in months:
        start = pd.Timestamp(m + "-01")
        end = start + pd.offsets.MonthBegin(1)
        ctx = build_context(load_m1(cfg, None, str(start.date()), str(end.date())), cfg)
        for s in engine.run(ctx)["setups"]:
            if s.get("status") != "FILLED" or np.isnan(s.get("r", np.nan)):
                continue
            mfe, mae = excursions(s, ctx.m1a)
            lt = s["signal_time"].tz_convert(cfg["local_timezone"])
            rows.append({
                "mesiac": m, "čas": lt.strftime("%d.%m %H:%M"), "hodina": lt.hour, "smer": s["direction"],
                "zóna": s["sweep_kind"], "sl_p": s["sl_pips"], "výsl": s["result"], "r": round(s["r"], 2),
                "mfe": mfe, "mae": mae, "d1_bias": s["d1_bias"], "w1_bias": s["w1_bias"], "h4_crt": s["h4_crt"],
                "adr": s["adr_used"], "pd": s["premium_discount"], "mo": s["midnight_open"], "idm": s["htf_idm"],
                "strong": s["strong_body"], "mss_bars": s["mss_bars"], "minúty": round((s["exit_ns"] - s["fill_ns"]) / 6e10),
            })
    return pd.DataFrame(rows)


def stats(r: np.ndarray) -> dict:
    w, l = r[r > 0].sum(), -r[r <= 0].sum()
    return {"n": len(r), "win%": round(100 * (r > 0).mean(), 1) if len(r) else 0.0,
            "PF": round(w / l, 2) if l > 0 else 99.0, "R": round(r.sum(), 1)}


def report(t: pd.DataFrame, days: int) -> str:
    out = []
    st = stats(t["r"].to_numpy())
    out.append(f"SPOLU {st} | {st['n'] / days:.2f} obchodu/deň | " +
               " | ".join(f"{m}: {stats(g['r'].to_numpy())}" for m, g in t.groupby("mesiac")))
    loss = t[t["r"] < 0]
    if len(loss):
        out.append(f"STRATY {len(loss)}: MFE pred SL: <0,3R {int((loss.mfe < 0.3).sum())}, 0,3–1R {int(((loss.mfe >= 0.3) & (loss.mfe < 1)).sum())}, "
                   f"≥1R {int((loss.mfe >= 1).sum())} | medián minút do SL {loss['minúty'].median():.0f} | medián SL {loss.sl_p.median():.1f} p")
    t = t.assign(hod=pd.cut(t["hodina"], [0, 11, 14, 17, 24], labels=["08-11", "11-14", "14-17", "17-19"]),
                 d1_v_smere=np.where(t["smer"] == "LONG", t["d1_bias"] == "bullish", t["d1_bias"] == "bearish"),
                 w1_v_smere=np.where(t["smer"] == "LONG", t["w1_bias"] == "bullish", t["w1_bias"] == "bearish"),
                 sl_b=pd.cut(t["sl_p"], [0, 5, 8, 12, 100]).astype(str), adr_b=pd.cut(t["adr"], [0, 0.5, 0.8, 5]).astype(str),
                 zona_tf=t["zóna"].str.split("_").str[0])
    for f in ("hod", "smer", "zona_tf", "d1_v_smere", "w1_v_smere", "h4_crt", "pd", "mo", "idm", "strong", "sl_b", "adr_b"):
        parts = [f"{v}: {g['r'].size} obch, {100 * (g['r'] > 0).mean():.0f} %, {g['r'].sum():+.1f}R" for v, g in t.groupby(f, observed=True)]
        out.append(f"  {f:10s} " + " | ".join(parts))
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config_htf.yaml")
    p.add_argument("--months", default="2025-03,2025-07,2025-08")
    p.add_argument("--label", default="")
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--trades", action="store_true", help="vypíš aj zoznam stratových obchodov")
    a = p.parse_args()
    months = a.months.split(",")
    t = run(a.config, months, a.set)
    print(report(t, 21 * len(months)))
    if a.trades:
        print(t[t["r"] < 0][["mesiac", "čas", "smer", "zóna", "sl_p", "mfe", "minúty", "d1_bias", "h4_crt", "adr"]].to_string(index=False))
    st = stats(t["r"].to_numpy())
    log = Path("reports/iteracie.csv")
    log.parent.mkdir(exist_ok=True)
    row = pd.DataFrame([{"kolo": a.label, "mesiace": a.months, "zmeny": ";".join(a.set), **st,
                         "za_deň": round(st["n"] / (21 * len(months)), 2)}])
    row.to_csv(log, mode="a", header=not log.exists(), index=False)


if __name__ == "__main__":
    main()
