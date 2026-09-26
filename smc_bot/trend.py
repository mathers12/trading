"""Trendový (momentum) intraday model.

Odvodený z analýzy obchodov používateľa (august 2026, USTEC): zisk vznikal iba pri obchodoch v smere trendu
(4h pohyb v smere + cena nad EMA200 na M5, horná polovica denného rozsahu pri long), najmä po breakoute 4h extrému.
Riadenie: tesný SL ~0,3 ATR(H1), posun na BE po +1R, potom trailing, bez pevného TP.

Nezávislý od SMC engine: vstup sú M1 sviečky (bid) z CSV (formát dukascopy-node) a pevný spread v bodoch.
Long a short jedným kódom: short sa počíta na zrkadlených cenách (x → −x, high ↔ low).
Signál vzniká na uzavretí M5 sviečky a vstup je na open nasledujúcej M1 sviečky (bez nazerania do budúcnosti).

    python -m smc_bot.trend --csv usatechidxusd-m1-bid-2025.csv --split 2025-07-01 --spread 1.5 [--grid]
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass, replace, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    trigger: str = "breakout"      # breakout | pullback | any
    ema_stack: bool = False        # vyžadovať EMA20 > EMA50 > EMA200 (v smere)
    dayloc: float = 0.5            # long iba ak je cena v hornej časti denného rozsahu (0 = vypnuté)
    sl_atr: float = 0.3            # SL = vstup − sl_atr × ATR(H1)
    be_r: float = 1.0              # po +be_r R posun SL na vstup (0 = vypnuté)
    trail_atr: float = 0.5         # po BE trailing sl = max − trail_atr × ATR (0 = vypnuté)
    tp_r: float = 0.0              # pevný TP v R (0 = bez TP)
    start_h: int = 6               # okno signálov v UTC [start_h, end_h)
    end_h: int = 20
    close_h: int = 21              # nútené zatvorenie (UTC hodina)
    direction: str = "both"        # both | long | short


def load_csv(path: str) -> pd.DataFrame:
    """M1 CSV z dukascopy-node (timestamp v ms, open, high, low, close) → o, h, l, c s UTC indexom."""
    x = pd.read_csv(path)
    if "timestamp" in x.columns:
        idx = pd.to_datetime(x["timestamp"], unit="ms")
        x = x.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    else:  # vlastný formát: prvý stĺpec čas, stĺpce o, h, l, c
        idx = pd.to_datetime(x.iloc[:, 0])
    df = x[["o", "h", "l", "c"]].astype(float)
    df.index = pd.DatetimeIndex(idx, name="t")
    return df[~df.index.duplicated()].sort_index()


def _mirror(m1: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"o": -m1.o, "h": -m1.l, "l": -m1.h, "c": -m1.c}, index=m1.index)


def features(m1: pd.DataFrame) -> pd.DataFrame:
    """Vlastnosti na M5 (pre long; short = features(_mirror(m1))). Index = čas uzavretia M5 sviečky."""
    m5 = m1.resample("5min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    m5.index = m5.index + pd.Timedelta(minutes=5)
    f = pd.DataFrame(index=m5.index)
    c = m5.c
    f["c"] = c
    f["ema20"], f["ema50"], f["ema200"] = (c.ewm(span=s, adjust=False).mean() for s in (20, 50, 200))
    f["ch4h"] = c - c.shift(48)
    f["hh4h"] = m5.h.rolling(48).max().shift(1)          # 4h maximum bez aktuálnej sviečky
    f["touch20"] = (m5.l <= f.ema20) & (c > f.ema20)
    day = (m5.index - pd.Timedelta(minutes=5)).floor("D")
    dhi = m5.h.groupby(day).cummax()
    dlo = m5.l.groupby(day).cummin()
    f["dayloc"] = (c - dlo) / (dhi - dlo).replace(0, np.nan)
    h1 = m1.resample("1h").agg({"h": "max", "l": "min"}).dropna()
    rng = (h1.h - h1.l).abs()
    atr = rng.rolling(24).mean()
    atr.index = atr.index + pd.Timedelta(hours=1)        # známe až po uzavretí hodiny
    f["atr"] = atr.reindex(f.index, method="ffill")
    return f


def signals(f: pd.DataFrame, p: Params) -> pd.Series:
    trend = (f.c > f.ema200) & (f.ch4h > 0)
    if p.ema_stack:
        trend &= (f.ema20 > f.ema50) & (f.ema50 > f.ema200)
    if p.dayloc > 0:
        trend &= f.dayloc >= p.dayloc
    brk = f.c > f.hh4h
    pb = f.touch20
    trig = {"breakout": brk, "pullback": pb, "any": brk | pb}[p.trigger]
    h = (f.index - pd.Timedelta(minutes=5)).hour
    ok = trend & trig & (h >= p.start_h) & (h < p.end_h) & f.atr.notna()
    return ok


def _simulate_side(m1: pd.DataFrame, f: pd.DataFrame, p: Params, spread: float, side: int) -> list[dict]:
    sig = signals(f, p)
    t_m1 = m1.index.values
    o, h, l = m1.o.values, m1.h.values, m1.l.values
    out = []
    busy_until = np.datetime64("1970-01-01")
    for T in f.index[sig.values]:
        if T.to_datetime64() < busy_until:
            continue
        i = t_m1.searchsorted(T.to_datetime64())
        if i >= len(t_m1) or t_m1[i] - T.to_datetime64() > np.timedelta64(10, "m"):
            continue
        atr = f.at[T, "atr"]
        entry = o[i] + spread                              # celý spread započítaný pri vstupe
        risk = p.sl_atr * atr
        sl = entry - risk
        tp = entry + p.tp_r * risk if p.tp_r > 0 else np.inf
        close_t = (T.floor("D") + pd.Timedelta(hours=p.close_h)).to_datetime64()
        if close_t <= T.to_datetime64():
            continue
        hw, be_done, exit_px, why, j = entry, False, None, "time", i
        for j in range(i, len(t_m1)):
            if t_m1[j] >= close_t:
                exit_px, why = o[j], "time"
                break
            if l[j] <= sl:                                 # SL má prednosť (konzervatívne)
                exit_px, why = min(sl, o[j]), ("be" if be_done and sl >= entry else "sl")
                break
            if j > i and h[j] >= tp:
                exit_px, why = max(tp, o[j]), "tp"
                break
            hw = max(hw, h[j])
            if p.be_r > 0 and not be_done and hw - entry >= p.be_r * risk:
                be_done, sl = True, max(sl, entry)
            if be_done and p.trail_atr > 0:
                sl = max(sl, hw - p.trail_atr * atr)
        if exit_px is None:
            exit_px, why = o[j], "end"
        r = (exit_px - entry) / risk
        out.append({"time": T, "side": side, "entry": side * entry, "exit": side * exit_px, "risk_pts": risk,
                    "R": r, "pts": exit_px - entry, "why": why, "exit_time": pd.Timestamp(t_m1[j])})
        busy_until = t_m1[j]
    return out


def backtest(m1: pd.DataFrame, p: Params, spread: float, feats: tuple | None = None) -> pd.DataFrame:
    fl, fs = feats or (features(m1), features(_mirror(m1)))
    rows = []
    if p.direction in ("both", "long"):
        rows += _simulate_side(m1, fl, p, spread, 1)
    if p.direction in ("both", "short"):
        rows += _simulate_side(_mirror(m1), fs, p, spread, -1)
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True) if rows else pd.DataFrame()


def stats(t: pd.DataFrame, days: int) -> dict:
    if not len(t):
        return {"n": 0, "per_day": 0.0, "win": np.nan, "PF": np.nan, "avgR": np.nan, "sumR": 0.0, "maxDD_R": 0.0}
    r = t.R
    loss = -r[r < 0].sum()
    eq = r.cumsum()
    return {"n": len(t), "per_day": round(len(t) / max(days, 1), 2), "win": round((r > 0).mean(), 3),
            "PF": round(r[r > 0].sum() / loss, 2) if loss > 0 else np.inf, "avgR": round(r.mean(), 3),
            "sumR": round(r.sum(), 1), "maxDD_R": round((eq.cummax() - eq).max(), 1)}


def _days(m1: pd.DataFrame, a=None, b=None) -> int:
    idx = m1.index
    if a is not None:
        idx = idx[idx >= a]
    if b is not None:
        idx = idx[idx < b]
    return int(pd.Series(idx.floor("D")).nunique())


def split_stats(m1: pd.DataFrame, t: pd.DataFrame, split: str | None) -> dict:
    res = {f"ALL_{k}": v for k, v in stats(t, _days(m1)).items()}
    if split:
        s = pd.Timestamp(split)
        ti = t[t.time < s] if len(t) else t
        to = t[t.time >= s] if len(t) else t
        res.update({f"IS_{k}": v for k, v in stats(ti, _days(m1, b=s)).items()})
        res.update({f"OOS_{k}": v for k, v in stats(to, _days(m1, a=s)).items()})
    return res


GRID = {
    "trigger": ["breakout", "pullback", "any"],
    "ema_stack": [False, True],
    "dayloc": [0.0, 0.5],
    "sl_atr": [0.3, 0.5],
    "exit": [("trail", 1.0, 0.5, 0.0), ("trail_wide", 1.0, 1.0, 0.0), ("tp2", 1.0, 0.0, 2.0), ("tp2_nobe", 0.0, 0.0, 2.0)],
    "window": [(6, 20), (12, 20)],
}


def run_grid(m1: pd.DataFrame, spread: float, split: str | None, base: Params = Params()) -> pd.DataFrame:
    feats = (features(m1), features(_mirror(m1)))
    rows = []
    keys = list(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        c = dict(zip(keys, combo))
        ex, be, tr, tp = c.pop("exit")
        sh, eh = c.pop("window")
        p = replace(base, **c, be_r=be, trail_atr=tr, tp_r=tp, start_h=sh, end_h=eh)
        t = backtest(m1, p, spread, feats)
        rows.append({**{k: v for k, v in asdict(p).items() if k in ("trigger", "ema_stack", "dayloc", "sl_atr", "start_h")},
                     "exit": ex, **split_stats(m1, t, split)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="Trendový momentum model – backtest na M1 CSV")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--split", help="začiatok OOS, napr. 2025-07-01")
    ap.add_argument("--spread", type=float, default=1.5, help="spread + poplatky v bodoch na obchod")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--out")
    for k, v in asdict(Params()).items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=(lambda s: s.lower() in ("1", "true", "yes")) if isinstance(v, bool) else type(v), default=v)
    a = ap.parse_args()
    m1 = load_csv(a.csv)
    if a.start:
        m1 = m1[m1.index >= pd.Timestamp(a.start)]
    if a.end:
        m1 = m1[m1.index < pd.Timestamp(a.end)]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(f"Dáta: {m1.index.min()} – {m1.index.max()}, {len(m1)} M1 sviečok, spread {a.spread} b.")
    if a.grid:
        g = run_grid(m1, a.spread, a.split)
        if a.out:
            g.to_csv(a.out, index=False)
        key = "IS_avgR" if a.split else "ALL_avgR"
        cols = [c for c in g.columns if not c.endswith("maxDD_R")]
        print(g.sort_values(key, ascending=False)[cols].head(25).to_string(index=False))
        if a.split:
            both = g[(g.IS_n >= 30) & (g.OOS_n >= 20)].copy()
            both["min_PF"] = both[["IS_PF", "OOS_PF"]].min(axis=1)
            print("\nNajrobustnejšie (lepší z horších PF v IS/OOS):")
            print(both.sort_values("min_PF", ascending=False)[cols + ["min_PF"]].head(15).to_string(index=False))
        return
    p = Params(**{k: getattr(a, k) for k in asdict(Params())})
    t = backtest(m1, p, a.spread)
    print(p)
    for k, v in split_stats(m1, t, a.split).items():
        print(f"  {k}: {v}")
    if len(t):
        print("\nVýstupy:", t.why.value_counts().to_dict())
        print("Podľa mesiacov (sumR, n):")
        print(t.groupby(t.time.dt.to_period("M")).R.agg(["size", "sum"]).round(1).T.to_string())
        if a.out:
            t.to_csv(a.out, index=False)


if __name__ == "__main__":
    main()
