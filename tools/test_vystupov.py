"""Rôzne mechanické výstupy na skutočných vstupoch z exportu (zlato)."""
import sys, itertools, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
from smc_bot.trade_forensics import load_trades
from smc_bot.gold_pullback import prepare, exit_trade, DEFAULT
tr = load_trades(sys.argv[1], "XAU/USD", "UTC")
m = pd.read_parquet("data/XAU_USD_M1_dukascopy.parquet").sort_index()
m = m[(m.index >= tr.t.min() - pd.Timedelta(days=3)) & (m.index <= tr.t_close.max() + pd.Timedelta(days=1))]
a = prepare(m)
J = [a["t"].searchsorted(t) for t in tr.t]
print(f"skutočné výstupy: win {100*(tr.pts>0).mean():.0f} %, PF {tr.pts[tr.pts>0].sum()/-tr.pts[tr.pts<=0].sum():.2f}, priem. {tr.pts.mean():+.2f}")
rows = []
for part, tp, trail, sl, be in itertools.product((0.0, 0.5), (3.0, 4.0, 6.0), (3.0, 5.0, 8.0), (10.0, 15.0), (False, True)):
    if part == 0 and (trail != 3.0 or be):
        continue
    p = {**DEFAULT, "partial": part, "tp": tp, "trail": trail, "sl": sl, "be_after_partial": be, "max_min": 480}
    r = np.array([exit_trade(a, j, d, p)[0] for j, d in zip(J, tr.dir)])
    rows.append({"čiast.": part, "tp": tp, "trail": trail if part else "-", "sl": sl, "BE": be,
                 "win%": round(100 * (r > 0).mean()), "PF": round(r[r > 0].sum() / -r[r <= 0].sum(), 2), "priem": round(r.mean(), 2)})
print(pd.DataFrame(rows).sort_values("PF", ascending=False).head(12).to_string(index=False))
