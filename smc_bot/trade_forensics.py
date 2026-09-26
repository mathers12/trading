"""Rozbor cudzích/ručných obchodov: kontext trhu pri vstupe (z M1 bid/ask dát Dukascopy).

python -m smc_bot.trade_forensics subor.xlsx --symbol XAU/USD --instrument XAU_USD [--tz UTC]
Porovná vlastnosti pri vstupoch s náhodnými okamihmi v tých istých hodinách a ziskové so stratovými.
"""
from __future__ import annotations

import argparse

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def load_trades(path: str, symbol: str, tz: str) -> pd.DataFrame:
    d = pd.read_excel(path) if path.endswith("xlsx") else pd.read_csv(path)
    d = d[d["Symbol"] == symbol].copy()
    d["t"] = pd.to_datetime(d["Opened"], format="%d/%m/%Y %H:%M").dt.tz_localize(tz).dt.tz_convert("UTC")
    d["t_close"] = pd.to_datetime(d["Closed"], format="%d/%m/%Y %H:%M").dt.tz_localize(tz).dt.tz_convert("UTC")
    d["dir"] = np.where(d["Direction"] == "BUY", 1, -1)
    d["pts"] = d["dir"] * (d["Close price"] - d["Open price"])
    return d.sort_values("t").reset_index(drop=True)


def features(m: pd.DataFrame, t: pd.Timestamp, d: int) -> dict:
    """Vlastnosti známe pred časom t (iba uzavreté sviečky), orientované v smere obchodu d (+ = v prospech smeru)."""
    mid = (m["bid_close"] + m["ask_close"]) / 2
    hi, lo = m["ask_high"], m["bid_low"]
    k = m.index.searchsorted(t, "left")  # prvá sviečka od t -> používame k-1 a staršie
    if k < 300:
        return {}
    p = mid.iat[k - 1]
    f = {}
    for n in (5, 15, 60, 240):
        f[f"pohyb_{n}m"] = d * (p - mid.iat[k - 1 - n])
    day0 = m.index.searchsorted(t.normalize(), "left")
    dh, dl = hi.iloc[day0:k].max(), lo.iloc[day0:k].min()
    f["poloha_v_dni"] = ((p - dl) / (dh - dl) if dh > dl else 0.5) if d == 1 else ((dh - p) / (dh - dl) if dh > dl else 0.5)
    seg = m.iloc[day0:k]
    vw = ((seg["bid_close"] + seg["ask_close"]) / 2 * seg["volume"].clip(lower=1e-9)).sum() / seg["volume"].clip(lower=1e-9).sum()
    f["vs_vwap"] = d * (p - vw)
    h60, l60 = hi.iloc[k - 60:k].max(), lo.iloc[k - 60:k].min()
    f["pullback_60m"] = ((h60 - p) / (h60 - l60) if d == 1 else (p - l60) / (h60 - l60)) if h60 > l60 else 0
    # sweep: za posledných 15 min cena prekonala extrém predošlých 60 min proti smeru obchodu a vrátila sa
    ref_lo, ref_hi = lo.iloc[k - 75:k - 15].min(), hi.iloc[k - 75:k - 15].max()
    rec_lo, rec_hi = lo.iloc[k - 15:k].min(), hi.iloc[k - 15:k].max()
    f["sweep_proti"] = bool(rec_lo < ref_lo and p > ref_lo) if d == 1 else bool(rec_hi > ref_hi and p < ref_hi)
    f["breakout_v_smere"] = bool(rec_hi > ref_hi) if d == 1 else bool(rec_lo < ref_lo)
    # trend H1/H4: close posledných uzavretých vs EMA20
    for tf, rule in (("H1", "1h"), ("H4", "4h")):
        c = mid.iloc[:k].resample(rule).last().dropna()
        if len(c) > 25:
            ema = c.ewm(span=20, adjust=False).mean()
            f[f"trend_{tf}"] = float(d * (c.iat[-2] - ema.iat[-2]) > 0)
    # M1 FVG v smere za posledných 15 min
    H, L = hi.iloc[k - 17:k].to_numpy(), lo.iloc[k - 17:k].to_numpy()
    f["fvg_m1"] = bool(((L[2:] > H[:-2]).any()) if d == 1 else ((H[2:] < L[:-2]).any()))
    f["okruhla_5$"] = round(min(p % 5, 5 - p % 5), 2)
    f["atr_15m"] = (hi.iloc[k - 15:k] - lo.iloc[k - 15:k]).mean()
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--symbol", default="XAU/USD")
    ap.add_argument("--instrument", default="XAU_USD")
    ap.add_argument("--tz", default="UTC")
    a = ap.parse_args()
    tr = load_trades(a.file, a.symbol, a.tz)
    m = pd.read_parquet(f"data/{a.instrument}_M1_dukascopy.parquet")
    rows = []
    for r in tr.itertuples():
        f = features(m, r.t, r.dir)
        if f:
            rows.append({**f, "win": r.pts > 0, "pts": r.pts, "dir": r.dir, "hod_utc": r.t.hour})
    F = pd.DataFrame(rows)
    # náhodné okamihy v tých istých hodinách a s rovnakým pomerom long/short
    rng = np.random.default_rng(0)
    base = []
    days = sorted({t.normalize() for t in tr["t"]})
    for _ in range(1500):
        day = days[rng.integers(len(days))]
        t = day + pd.Timedelta(hours=int(rng.choice(F["hod_utc"])), minutes=int(rng.integers(60)))
        f = features(m, t, int(rng.choice(F["dir"])))
        if f:
            base.append(f)
    B = pd.DataFrame(base)
    print(f"obchodov s dátami: {len(F)}, náhodných okamihov: {len(B)}")
    print(f"{'vlastnosť':16s} {'vstupy':>9s} {'náhodne':>9s} {'zisky':>9s} {'straty':>9s}")
    for c in B.columns:
        yn = B[c].dtype == bool or c.startswith("trend")
        agg = (lambda s: f"{100 * s.astype(float).mean():8.0f}%") if yn else (lambda s: f"{s.median():9.2f}")
        print(f"{c:16s} {agg(F[c].dropna())} {agg(B[c].dropna())} {agg(F[F.win][c].dropna())} {agg(F[~F.win][c].dropna())}")
    # jednoduché pravidlá: úspešnosť a priemer bodov podľa súladu s 60 min trendom a VWAP
    F["s_60m"] = F["pohyb_60m"] > 0
    F["s_vwap"] = F["vs_vwap"] > 0
    for keys in (["s_60m"], ["s_vwap"], ["trend_H1"], ["s_60m", "s_vwap"]):
        g = F.groupby(keys)
        print("  " + " | ".join(f"{keys}={k}: n={len(x)}, win {100 * x.win.mean():.0f} %, priem. {x.pts.mean():+.2f} $" for k, x in g))
    # následné pohyby po vstupe (priemer v smere obchodu) - ako rýchlo prichádza zisk
    fw = []
    mid = (m["bid_close"] + m["ask_close"]) / 2
    for r in tr.itertuples():
        k = m.index.searchsorted(r.t)
        if k + 60 < len(m):
            fw.append([r.dir * (mid.iat[k + n] - mid.iat[k]) for n in (1, 5, 15, 30, 60)])
    fw = np.array(fw)
    print("priemerný pohyb v smere obchodu po vstupe (USD): " + ", ".join(f"{n} min {v:+.2f}" for n, v in zip((1, 5, 15, 30, 60), fw.mean(0))))


if __name__ == "__main__":
    main()
