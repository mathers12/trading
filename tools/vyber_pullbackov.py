"""Čím sa líšia pullbacky, ktoré trader vzal, od tých, ktoré v tom istom čase nechal tak (zlato, časy UTC)."""
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
from smc_bot.gold_pullback import DEFAULT, prepare  # noqa: E402
from smc_bot.trade_forensics import load_trades  # noqa: E402

tr = load_trades(sys.argv[1], "XAU/USD", "UTC")
m = pd.read_parquet("data/XAU_USD_M1_dukascopy.parquet").sort_index()
m = m[(m.index >= tr.t.min().normalize() - pd.Timedelta(days=5)) & (m.index <= tr.t_close.max() + pd.Timedelta(hours=2))]
a = prepare(m)
mid = a["mid"]
o, c = (m["bid_open"] + m["ask_open"]).to_numpy() / 2, (m["bid_close"] + m["ask_close"]).to_numpy() / 2
h, l = m["ask_high"].to_numpy(), m["bid_low"].to_numpy()
vol = m["volume"].to_numpy()
idx = m.index
day = idx.normalize()
d1 = m.resample("1D").agg({"ask_high": "max", "bid_low": "min"})
pdh = pd.Series(d1["ask_high"].shift(1).reindex(day).to_numpy(), index=idx)
pdl = pd.Series(d1["bid_low"].shift(1).reindex(day).to_numpy(), index=idx)
asia = (idx.hour < 7)
ah = pd.Series(np.where(asia, h, np.nan), index=idx).groupby(day).cummax().groupby(day).ffill()
al = pd.Series(np.where(asia, l, np.nan), index=idx).groupby(day).cummin().groupby(day).ffill()
NEWS_UTC = {(12, 30), (14, 0), (18, 0)}  # typické časy amerických dát (08:30, 10:00, 14:00 NY v lete)


def feats(k: int, d: int) -> dict:
    """k = index vstupnej sviečky (vstup na jej otvorení), používa iba sviečky < k."""
    p = c[k - 1]
    body = c[k - 1] - o[k - 1]
    prev_body = c[k - 2] - o[k - 2]
    rng = h[k - 1] - l[k - 1]
    lower_wick = min(o[k - 1], c[k - 1]) - l[k - 1]
    upper_wick = h[k - 1] - max(o[k - 1], c[k - 1])
    f = {
        "sviečka_v_smere": float(d * body > 0),
        "engulfing": float(d * body > 0 and d * prev_body < 0 and abs(body) > abs(prev_body)),
        "pin_bar": float(rng > 0 and ((lower_wick if d == 1 else upper_wick) / rng) > 0.5),
        "objem_5m/60m": vol[k - 5:k].mean() / max(vol[k - 65:k - 5].mean(), 1e-9),
        "rozsah_5m/60m": (h[k - 5:k].max() - l[k - 5:k].min()) / max(h[k - 65:k].max() - l[k - 65:k].min(), 1e-9),
    }
    lv = [pdh.iat[k - 1], pdl.iat[k - 1], ah.iat[k - 1], al.iat[k - 1], np.round(p / 10) * 10]
    lv = [x for x in lv if x == x]
    f["vzdial_od_hladiny"] = min(abs(p - x) for x in lv) if lv else np.nan
    sw = l[k - 30:k].min() if d == 1 else h[k - 30:k].max()  # extrém pullbacku = potenciálne S/R
    f["od_extrému_30m"] = d * (p - sw)
    t = idx[k]
    f["min_od_správ"] = min(abs((t.hour * 60 + t.minute) - (hh * 60 + mm)) for hh, mm in NEWS_UTC)
    return f


# kandidáti: pullbacky podľa mechanického pravidla (bez obmedzenia hodín) + skutočné vstupy
p = {**DEFAULT, "hours": (0, 24), "pull": 1.5}
cand = []
n = len(mid)
for i in range(80, n - 1):
    d = int(np.sign(mid[i - 15] - mid[i - 75]))
    if d == 0 or d * (mid[i] - mid[i - 15]) > -p["pull"]:
        continue
    cand.append((i + 1, d))
taken = {(a["t"].searchsorted(t), d) for t, d in zip(tr.t, tr.dir)}
tk = {k for k, _ in taken}
rows = []
for k, d in cand:
    if any(abs(k - x) <= 3 for x in tk):
        continue  # blízko skutočného vstupu -> nie je „nevzatý“
    if idx[k] < tr.t.min() or idx[k] > tr.t.max() or not (idx[k].hour in set(tr.t.dt.hour)):
        continue
    rows.append({**feats(k, d), "vzal": 0})
for k, d in taken:
    rows.append({**feats(k, d), "vzal": 1})
F = pd.DataFrame(rows)
# chovanie tradera: čas od predošlého obchodu a výsledok predošlého
tr = tr.sort_values("t")
gap = (tr.t - tr.t_close.shift()).dt.total_seconds() / 60
prev_win = (tr.pts.shift() > 0)
print(f"kandidátov (nevzatých): {int((F.vzal == 0).sum())}, vzatých: {int(F.vzal.sum())}")
print(f"{'vlastnosť':20s} {'vzal':>8s} {'nevzal':>8s}")
for col in [x for x in F.columns if x != "vzal"]:
    yn = F[col].dropna().isin([0, 1]).all()
    fmt = (lambda s: f"{100 * s.mean():7.0f}%") if yn else (lambda s: f"{s.median():8.2f}")
    print(f"{col:20s} {fmt(F[F.vzal == 1][col].dropna())} {fmt(F[F.vzal == 0][col].dropna())}")
print(f"\nčas od predošlého obchodu (medián min): {gap.median():.0f}; do 5 min: {100 * (gap <= 5).mean():.0f} %")
print(f"po zisku ďalší obchod vyhral: {100 * (tr.pts > 0)[prev_win].mean():.0f} %, po strate: {100 * (tr.pts > 0)[~prev_win & tr.pts.shift().notna()].mean():.0f} %")
same = (tr.dir == tr.dir.shift())
print(f"rovnaký smer ako predošlý obchod: {100 * same.mean():.0f} % (win {100 * (tr.pts > 0)[same].mean():.0f} %), opačný: win {100 * (tr.pts > 0)[~same].mean():.0f} %")
