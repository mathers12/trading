"""Prepínač podľa režimu: trend (vysoký H1 efficiency ratio) -> pullback, bočný trh (nízky ER) -> obrat k VWAP. Zlato, UTC."""
import sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
from smc_bot import gold_pullback as gp, gold_reversal as gr
from smc_bot.gold_pullback import load, stats

PB = {**gp.DEFAULT, "tp": 15.0, "sl": 10.0, "max_min": 240}
RV = {**gr.DEFAULT, "vwap_dist": 15.0, "drop4h": 10.0, "tp": 0.0, "sl": 25.0, "pos": 0.3}


def er_series(a: dict, hours: int) -> np.ndarray:
    """ER z uzavretých H1 sviečok za posledných `hours` hodín, zarovnané na M1 (známe až po uzavretí H1)."""
    mid = pd.Series(a["mid"], index=a["t"])
    h1 = mid.resample("1h").last().dropna()
    net = (h1 - h1.shift(hours)).abs()
    path = h1.diff().abs().rolling(hours).sum()
    er = (net / path).shift(1)  # hodnota platí po uzavretí sviečky
    er.index = er.index + pd.Timedelta(hours=1)
    return er.reindex(a["t"], method="ffill").to_numpy()


def run(months, lo, hi, hours=24, which="oba"):
    data = load(months)
    res = {}
    for mo, a in data.items():
        er = er_series(a, hours)
        pts = []
        if which in ("oba", "pullback"):
            pts += [x["pts"] for x in gp.simulate(a, {**PB, "allow": np.nan_to_num(er) >= hi}) if str(x["t"])[:7] == mo]
        if which in ("oba", "obrat"):
            pts += [x["pts"] for x in gr.simulate(a, {**RV, "allow": (er <= lo) & ~np.isnan(er)}) if str(x["t"])[:7] == mo]
        res[mo] = np.array(pts)
    return res


if __name__ == "__main__":
    months = sys.argv[1].split(",")
    for lo, hi in ((0.15, 0.4), (0.2, 0.5), (0.25, 0.35), (0.1, 0.6)):
        for which in ("pullback", "obrat", "oba"):
            res = run(months, lo, hi, which=which)
            allp = np.concatenate(list(res.values()))
            per = " | ".join(f"{mo}: PF {x[x>0].sum()/max(-x[x<=0].sum(),1e-9):.2f} ({len(x)})" for mo, x in res.items())
            print(f"ER bočný<={lo} trend>={hi} {which:9s} {stats(allp, 21*len(months))} || {per}", flush=True)
