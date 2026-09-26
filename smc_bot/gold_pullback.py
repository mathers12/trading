"""Scalp pullbackov na zlate (odvodené z rozboru 147 ručných obchodov, trade_forensics.py).

Smer: H1 trend (close poslednej uzavretej H1 vs EMA20) a strana dennej VWAP musia súhlasiť.
Vstup: za posledných `lookback` minút išla cena aspoň `pull` USD proti smeru -> vstup trhom na otvorení ďalšej M1.
Výstup: TP `tp` USD, SL `sl` USD, časový limit `max_min` minút. Jeden obchod naraz. Časy v UTC.
Simulácia konzervatívne na M1 bid/ask: long vstup ask / výstup bid, SL aj TP v jednej sviečke = SL.

python -m smc_bot.gold_pullback --months 2026-06,2026-07,2026-08 [--grid]
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

DEFAULT = {"lookback": 15, "pull": 2.0, "tp": 5.0, "sl": 8.0, "max_min": 60, "hours": (7, 19), "vwap": True, "trend": True,
           "dir_60m": 0.0, "partial": 0.0, "trail": 4.0, "be_after_partial": False,
           "bounce": 0.0, "bounce_wait": 30}  # dir_60m > 0: smer = 60-min pohyb (aspoň tento počet USD) namiesto H1 EMA


def prepare(m: pd.DataFrame) -> dict:
    mid = (m["bid_close"] + m["ask_close"]) / 2
    h1 = mid.resample("1h").last().dropna()
    ema = h1.ewm(span=20, adjust=False).mean()
    # H1 trend známy až po uzavretí H1 sviečky -> posun o 1 h dopredu
    tr = np.sign(h1 - ema)
    tr.index = tr.index + pd.Timedelta(hours=1)
    trend = tr.reindex(m.index, method="ffill").fillna(0).to_numpy()
    day = m.index.normalize()
    vol = m["volume"].clip(lower=1e-9)
    pv = (mid * vol).groupby(day).cumsum()
    vw = (pv / vol.groupby(day).cumsum()).to_numpy()
    return {"t": m.index, "mid": mid.to_numpy(), "vwap": vw, "trend": trend, "hour": m.index.hour.to_numpy(),
            "ao": m["ask_open"].to_numpy(), "bo": m["bid_open"].to_numpy(),
            "ah": m["ask_high"].to_numpy(), "al": m["ask_low"].to_numpy(),
            "bh": m["bid_high"].to_numpy(), "bl": m["bid_low"].to_numpy(),
            "day": day.to_numpy()}


def exit_trade(a: dict, j: int, d: int, p: dict) -> tuple[float, int]:
    """Výstup obchodu od vstupu na otvorení sviečky j. Vracia (výsledok v USD na celú pozíciu, index konca).

    partial > 0: časť pozície `partial` sa zatvorí na TP `tp`, zvyšok ide na trailing stop `trail` USD od najlepšej ceny
    (SL zvyšku sa po čiastočnom TP presunie aspoň na vstup iba ak be_after_partial). Inak jednoduchý TP/SL.
    """
    n = len(a["t"])
    e = a["ao"][j] if d == 1 else a["bo"][j]
    part = p.get("partial", 0.0)
    sl_px = e - d * p["sl"]
    done_part, best, realized = False, e, 0.0
    end = min(n - 1, j + p["max_min"])
    k = j
    while k <= end:
        if k > j and a["day"][k] != a["day"][j]:  # neprenášať cez polnoc UTC
            break
        lo, hi = (a["bl"][k], a["bh"][k]) if d == 1 else (a["al"][k], a["ah"][k])
        rest = 1.0 - part if done_part else 1.0
        if (lo <= sl_px) if d == 1 else (hi >= sl_px):  # konzervatívne: SL pred TP v tej istej sviečke
            return realized + rest * d * (sl_px - e), k
        fav = hi if d == 1 else lo
        if k > j:
            if not done_part and d * (fav - e) >= p["tp"]:
                if part <= 0:
                    return p["tp"], k
                realized += part * p["tp"]
                done_part = True
                if p.get("be_after_partial"):
                    sl_px = e if d == 1 else e
                    sl_px = max(sl_px, e) if d == 1 else min(sl_px, e)
            if done_part:
                best = max(best, fav) if d == 1 else min(best, fav)
                trail_px = best - d * p["trail"]
                sl_px = max(sl_px, trail_px) if d == 1 else min(sl_px, trail_px)
        k += 1
    k = min(k, n - 1)
    x = a["bo"][k] if d == 1 else a["ao"][k]
    rest = 1.0 - part if done_part else 1.0
    return realized + rest * d * (x - e), k


def simulate(a: dict, p: dict) -> list[dict]:
    n = len(a["mid"])
    lb, pull = p["lookback"], p["pull"]
    out, i = [], lb + 1
    h0, h1 = p["hours"]
    while i < n - 1:
        h = a["hour"][i]
        if not (h0 <= h < h1):
            i += 1
            continue
        d = int(a["trend"][i]) if p["trend"] else 0
        if p["dir_60m"] > 0 and i >= 75:
            m60 = a["mid"][i - lb] - a["mid"][i - lb - 60]  # 60 min pred pullbackom
            d = int(np.sign(m60)) if abs(m60) >= p["dir_60m"] else 0
        vw_side = np.sign(a["mid"][i] - a["vwap"][i])
        if not p["trend"] and p["dir_60m"] <= 0:
            d = int(vw_side)
        if d == 0 or (p["vwap"] and vw_side != d):
            i += 1
            continue
        move = d * (a["mid"][i] - a["mid"][i - lb])  # pohyb za lookback min v smere d
        if move > -pull:
            i += 1
            continue
        # voliteľne čakať na odraz od extrému pullbacku (bounce USD), najviac bounce_wait minút
        if p.get("bounce", 0) > 0:
            ext, jj = a["mid"][i], None
            for q in range(i + 1, min(n - 1, i + p.get("bounce_wait", 30))):
                ext = min(ext, a["mid"][q]) if d == 1 else max(ext, a["mid"][q])
                if d * (a["mid"][q] - ext) >= p["bounce"]:
                    jj = q
                    break
            if jj is None:
                i += 1
                continue
            i = jj
        # vstup na otvorení ďalšej sviečky
        j = i + 1
        res, k = exit_trade(a, j, d, p)
        out.append({"t": a["t"][j], "dir": d, "pts": res})
        i = k + 1
    return out


def stats(pts: np.ndarray, days: int) -> str:
    if not len(pts):
        return "n=0"
    w, l = pts[pts > 0].sum(), -pts[pts <= 0].sum()
    return (f"n={len(pts):4d} ({len(pts) / days:4.1f}/deň) win {100 * (pts > 0).mean():3.0f} % PF {w / l if l else 99:4.2f} "
            f"spolu {pts.sum():+7.1f} USD, priem. {pts.mean():+.2f}")


def load(months: list[str]) -> dict:
    m = pd.read_parquet("data/XAU_USD_M1_dukascopy.parquet").sort_index()
    return {mo: prepare(m[(m.index >= pd.Timestamp(mo + "-01", tz="UTC") - pd.Timedelta(days=3))
                          & (m.index < pd.Timestamp(mo + "-01", tz="UTC") + pd.offsets.MonthBegin(1))]) for mo in months}


def run(data: dict, p: dict) -> tuple[np.ndarray, dict]:
    per, allp = {}, []
    for mo, a in data.items():
        tr = [x for x in simulate(a, p) if str(x["t"])[:7] == mo]
        pts = np.array([x["pts"] for x in tr])
        per[mo] = pts
        allp.append(pts)
    return np.concatenate(allp) if allp else np.array([]), per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-06,2026-07,2026-08")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="napr. pull=2.5")
    a = ap.parse_args()
    months = a.months.split(",")
    data = load(months)
    days = 21 * len(months)
    base = dict(DEFAULT)
    for kv in a.set:
        k, v = kv.split("=")
        base[k] = type(DEFAULT[k])(eval(v)) if not isinstance(DEFAULT[k], bool) else v.lower() == "true"
    if not a.grid:
        pts, per = run(data, base)
        print("SPOLU", stats(pts, days))
        for mo, x in per.items():
            print("  ", mo, stats(x, 21))
        return
    rows = []
    for pull, tp, sl in itertools.product((1.5, 2.5, 4.0), (3.0, 5.0, 8.0), (6.0, 10.0)):
        p = {**base, "pull": pull, "tp": tp, "sl": sl}
        pts, per = run(data, p)
        w, l = pts[pts > 0].sum(), -pts[pts <= 0].sum()
        rows.append({"pull": pull, "tp": tp, "sl": sl, "n": len(pts), "win%": round(100 * (pts > 0).mean()),
                     "PF": round(w / l, 2) if l else 99, "USD": round(pts.sum(), 1),
                     "min_mes_PF": round(min((x[x > 0].sum() / max(-x[x <= 0].sum(), 1e-9)) for x in per.values()), 2)})
    print(pd.DataFrame(rows).sort_values("PF", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
