"""Mechanická verzia XAU stratégie: bias D1, London okno, prerazenie 60-min rozsahu na M5, SL 10 $, BE +1R, pyramída, zatvorenie 19:00 SK."""
import sys, itertools
import numpy as np, pandas as pd

P = pd.read_pickle(sys.argv[1] if len(sys.argv) > 1 else 'xau_all.pkl')
if P.index.tz is None:
    P.index = P.index.tz_localize('UTC')
ny = P.index.tz_convert('America/New_York')
P['tday'] = (ny + pd.Timedelta('7h')).date
sk = P.index.tz_convert('Europe/Bratislava')
P['skm'] = sk.hour * 60 + sk.minute
D = P.groupby('tday').agg(o=('open', 'first'), h=('high', 'max'), l=('low', 'min'), c=('close', 'last'), n=('close', 'size'))
D = D[D.n > 300]
D['ema'] = D.c.ewm(span=50).mean()
tr = pd.concat([D.h - D.l, (D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs()], axis=1).max(axis=1)
D['atr'] = tr.rolling(14).mean()
D['bias_ema'] = np.sign(D.c - D.ema).shift()
D['bias_prev'] = np.sign(D.c - D.o).shift()
D[['atr']] = D[['atr']].shift()
COMM = 0.09  # $/oz za obchod (0,25 lota = 2,22 $)
days = {k: g for k, g in P.groupby('tday')}


def run_day(g, s, cfg, atr):
    if s > 0:
        lo, hi, eo, xo = g.bid_low.values, g.bid_high.values, g.ask_open.values, g.bid_open.values
        mh, ml = g.high.values, g.low.values
    else:
        lo, hi, eo, xo = -g.ask_high.values, -g.ask_low.values, -g.bid_open.values, -g.ask_open.values
        mh, ml = -g.low.values, -g.high.values
    skm = g.skm.values
    R = cfg['sl']
    # M5 trigger: close nad max. predošlých 60 min, koniec M5 sviečky v okne
    trig = None
    for i in range(64, len(g) - 1):
        if (skm[i] + 1) % 5:
            continue
        m = skm[i] + 1
        if not any(a * 60 <= m < b * 60 for a, b in cfg['win']):
            continue
        c = (g.close.values[i] if s > 0 else -g.close.values[i])
        if c > mh[i - 64:i - 4].max():
            if cfg['k'] and (mh[:i + 1].max() - ml[:i + 1].min()) > cfg['k'] * atr:
                return None
            trig = i + 1
            break
    if trig is None:
        return None
    end = np.searchsorted(skm >= 19 * 60, True) if (skm >= 19 * 60).any() else len(g) - 1
    if trig >= end:
        return None
    e0 = eo[trig]
    pos = [[e0, e0 - R, True]]  # vstup, SL, otvorená
    res = []
    nadd = 0
    for j in range(trig, end):
        for p in pos:  # najprv SL (konzervatívne)
            if p[2] and lo[j] <= p[1]:
                res.append(p[1] - p[0]); p[2] = False
        if not any(p[2] for p in pos):
            break
        for p in pos:
            if p[2] and hi[j] >= p[0] + cfg['be'] * R:
                p[1] = max(p[1], p[0] + 0.1)
            if p[2] and cfg['tp'] and hi[j] >= p[0] + cfg['tp'] * R and j > trig:
                res.append(cfg['tp'] * R); p[2] = False
        while nadd < cfg['adds'] and hi[j] >= e0 + (nadd + 1) * R:
            nadd += 1
            lvl = e0 + nadd * R + (eo[j] - xo[j])  # + spread
            pos.append([lvl, lvl - R, True])
    for p in pos:
        if p[2]:
            res.append(xo[end] - p[0])
    return [(r - COMM) / R for r in res]


def backtest(cfg):
    out = []
    for day, row in D.iterrows():
        if pd.isna(row.atr) or day not in days:
            continue
        if cfg['bias'] == 'long':
            s = 1
        else:
            s = row['bias_' + cfg['bias']]
        if cfg.get('longonly') and s < 0:
            continue
        if not s:
            continue
        r = run_day(days[day], int(s), cfg, row.atr)
        if r is not None:
            out.append((pd.Timestamp(day), s, sum(r), len(r)))
    return pd.DataFrame(out, columns=['day', 's', 'R', 'n'])


def summ(t):
    if t.empty:
        return dict(dni=0, R=0, PF=np.nan, WR=np.nan)
    w, l = t.R[t.R > 0].sum(), -t.R[t.R < 0].sum()
    return dict(dni=len(t), obch=int(t.n.sum()), R=round(t.R.sum(), 1), PF=round(w / l, 2) if l else np.inf, WR=round((t.R > 0).mean(), 2))


if __name__ == '__main__':
    base = dict(sl=10, be=1, tp=0)
    rows = []
    for bias, adds, k, win in itertools.product(['ema', 'prev', 'long'], [0, 3], [0.5, 0], ['L', 'LN']):
        cfg = dict(base, bias=bias, adds=adds, k=k, win=[(8, 12)] if win == 'L' else [(8, 12), (15, 17)])
        t = backtest(cfg)
        oos = t[t.day < '2026-08-01']; aug = t[t.day >= '2026-08-01']
        a, b = summ(oos), summ(aug)
        rows.append(dict(bias=bias, adds=adds, k=k, win=win, **{f'{x}_2509-2607': y for x, y in a.items()}, **{f'{x}_aug': y for x, y in b.items()}))
    print(pd.DataFrame(rows).to_string())
