"""Systematické hľadanie pravidiel s výhodou (edge) bez preučenia.

Dáta sa rozdelia na dve časti:
  * in-sample (IS): na nich sa vyberajú najlepšie kombinácie pravidiel
  * out-of-sample (OOS): novšie dáta, ktoré výber "nevidel" – slúžia iba na overenie
Kombinácia má reálnu výhodu iba vtedy, ak funguje aj na OOS.
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
import os

import numpy as np
import pandas as pd

from . import engine
from .config import apply_overrides

def _j(x) -> str:
    """Hodnota pre --set (JSON je platný YAML)."""
    import json

    return json.dumps(x, ensure_ascii=False)


HTF_LIQ = ["PWH", "PWL", "PDH", "PDL", "ASIA_H", "ASIA_L"]
# okná v miestnom čase (Bratislava); celé obchodné okno je 08:00–19:00
WINDOWS = {
    "08-19": ["08:00-19:00"],
    "LO 08-11": ["08:00-11:00"],
    "LO+NY": ["08:00-11:00", "13:00-17:00"],
    "SB": ["09:00-10:00", "16:00-17:00"],  # ICT Silver Bullet (03–04 a 10–11 NY)
}

# dimenzia -> {popis: [--set prepínače]}
GRID = {
    "liq": {"všetka": ["filters.sweep_kinds=null"], "HTF": [f"filters.sweep_kinds={_j(HTF_LIQ)}"]},
    "okno": {k: [f"filters.signal_windows_local={_j(v)}"] for k, v in WINDOWS.items()},
    "vstup": {"FVG": ["entry.type=fvg_or_ob", "entry.price=proximal"], "OTE": ["entry.type=ote"]},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv": ["target.mode=first_min_rr"]},
    "MO": {"-": ["filters.require_midnight_open=false"], "áno": ["filters.require_midnight_open=true"]},
    "h4crt": {"-": ["filters.require_h4_crt=false"], "áno": ["filters.require_h4_crt=true"]},
    "slbuf": {"1": ["entry.sl_buffer_pips=1"], "3": ["entry.sl_buffer_pips=3"]},
}
# 2. kolo: okolo prvkov, ktoré pomohli v IS aj OOS (OTE, Londýn/Silver Bullet, H4 CRT, širší SL)
GRID_ICT2 = {
    "liq": GRID["liq"],
    "okno": {"LO 08-11": [f"filters.signal_windows_local={_j(WINDOWS['LO 08-11'])}"],
             "SB": [f"filters.signal_windows_local={_j(WINDOWS['SB'])}"],
             "LO+SB": [f"filters.signal_windows_local={_j(['08:00-11:00', '16:00-17:00'])}"]},
    "ote": {str(x): ["entry.type=ote", f"entry.ote_level={x}"] for x in (0.62, 0.705, 0.79)},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
    "h4crt": GRID["h4crt"],
    "slbuf": {"3": ["entry.sl_buffer_pips=3"], "5": ["entry.sl_buffer_pips=5"]},
}
# denný model: viac obchodov (cieľ >= 1 obchod denne na EUR/USD + GBP/USD)
GRID_DAILY = {
    "liq": {"všetka": ["filters.sweep_kinds=null"],
            "HTF+LON": [f"filters.sweep_kinds={_j(HTF_LIQ + ['LON_H', 'LON_L'])}"]},
    "okno": {"08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"],
             "LO+NY": [f"filters.signal_windows_local={_j(WINDOWS['LO+NY'])}"]},
    "vstup": {"FVG": ["entry.type=fvg_or_ob", "entry.price=proximal"], "OTE": ["entry.type=ote"]},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
    "h4crt": GRID["h4crt"],
    "bias": {"D1+": ["bias.filter=true"], "-": ["bias.filter=false"]},
}
# viac obchodov z najlepšieho setupu: dlhšia platnosť limitky, voľnejšie limity, širšie okná
GRID_FREQ = {
    "okno": {"LO+SB": [f"filters.signal_windows_local={_j(['08:00-11:00', '16:00-17:00'])}"],
             "LO+NYAM": [f"filters.signal_windows_local={_j(['08:00-11:00', '14:30-17:00'])}"],
             "08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"]},
    "expir": {"60m": ["entry.expiry_minutes=60"], "180m": ["entry.expiry_minutes=180"]},
    "limity": {"1naraz/2": ["risk.one_position_at_a_time=true", "risk.max_trades_per_day=2"],
               "viac/4": ["risk.one_position_at_a_time=false", "risk.max_trades_per_day=4", "risk.max_losses_per_day=3"]},
    "h4crt": GRID["h4crt"],
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
}
BEST = ["entry.type=ote", "entry.ote_level=0.705", "entry.sl_buffer_pips=3", "filters.sweep_kinds=null"]
# trhový vstup po MSS (bez čakania na pullback, ktorý vyberal horšie obchody)
GRID_MARKET = {
    "okno": {"08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"],
             "LO+NYAM": [f"filters.signal_windows_local={_j(['08:00-11:00', '14:30-17:00'])}"],
             "LO": [f"filters.signal_windows_local={_j(WINDOWS['LO 08-11'])}"]},
    "liq": GRID_DAILY["liq"],
    "h4crt": GRID["h4crt"],
    "bias": GRID_DAILY["bias"],
    "tp": GRID_DAILY["tp"],
    "MO": GRID["MO"],
}
# POI / supply-demand zóny (OB s displacementom, FVG a BOS na H4/H1/M15, iba prvý dotyk) -> M5 MSS
POI_BASE = ['liquidity.external=["poi_zone"]', "liquidity.internal_fvg_timeframes=[]"]
GRID_POI = {
    "zona": {"všetky": ["filters.sweep_kinds=null"], "H4": ['filters.sweep_kinds=["H4_OB"]'],
             "H1": ['filters.sweep_kinds=["H1_OB"]'], "M15": ['filters.sweep_kinds=["M15_OB"]']},
    "vstup": {"FVG": ["entry.type=fvg_or_ob"], "trh": ["entry.type=market"]},
    "sl": {"extrém": ["poi.sl=extreme"], "zóna": ["poi.sl=zone"]},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
    "bias": {"D1+": ["bias.filter=true"], "-": ["bias.filter=false"]},
    "sweep": {"-": ["filters.require_poi_sweep=false"], "áno": ["filters.require_poi_sweep=true"]},
    "idm": {"-": ["filters.require_inducement=false"], "áno": ["filters.require_inducement=true"]},
}
_LW = lambda *w: [f"session_strategy.windows_local={_j(list(w))}"]  # noqa: E731
_SS = lambda k, vals: {str(v): [f"session_strategy.{k}={_j(v)}"] for v in vals}  # noqa: E731
GRID_ASIA_BO = {  # 2. Londýnsky breakout ázijského rozsahu
    "okno": {"08-11": _LW("08:00-11:00"), "08-13": _LW("08:00-13:00")},
    "smer": _SS("direction", ["none", "d1", "h4"]),
    "vstup": _SS("entry", ["market", "limit"]),
    "sl": _SS("sl", ["mid", "opposite"]),
    "rr": _SS("rr", [1.5, 2.0, 3.0]),
    "max_rng": _SS("max_range_pips", [25, 40]),
}
GRID_ASIA_FO = {  # 3. Judas / turtle soup: falošné prerazenie Ázie v Londýne
    "okno": {"08-11": _LW("08:00-11:00"), "08-13": _LW("08:00-13:00")},
    "smer": _SS("direction", ["none", "d1"]),
    "vstup": _SS("entry", ["market", "limit"]),
    "tp": {"opačná": ["session_strategy.tp=opposite"], "stred": ["session_strategy.tp=mid"],
           "2R": ["session_strategy.tp=rr", "session_strategy.rr=2"]},
    "sweep": _SS("sweep_pips", [1, 3]),
    "slbuf": _SS("sl_buffer_pips", [1, 3]),
    "min_rr": _SS("min_rr", [1.0, 2.0]),
}
GRID_VA = {  # 4. návrat do value area predchádzajúceho dňa (pravidlo 80 %)
    "okno": {"08-19": _LW("08:00-19:00"), "08-13": _LW("08:00-13:00"), "14-19": _LW("14:00-19:00")},
    "smer": _SS("direction", ["none", "d1_not"]),
    "potvrd": _SS("confirm_bars", [3, 6, 12]),
    "tp": {"opačná": ["session_strategy.tp=opposite"], "POC": ["session_strategy.tp=poc"],
           "2R": ["session_strategy.tp=rr", "session_strategy.rr=2"]},
    "min_rr": _SS("min_rr", [1.0, 1.5]),
}
_LW_ = lambda *w: [f"filters.signal_windows_local={_j(list(w))}"]  # noqa: E731
# HTF pullback (dokument používateľa): BOS na H1/H4 -> golden pocket + FVG (H1/M15) -> inducement -> LTF MSS
HTF_BASE = ['liquidity.external=["htf_pb"]', "liquidity.internal_fvg_timeframes=[]", "bias.filter=false",
            'htf.poi_timeframe=["H1","M15"]']
GRID_HTF = {
    "vstup": {"FVG": ["entry.type=fvg_or_ob"], "trh": ["entry.type=market"], "OTE": ["entry.type=ote", "entry.ote_level=0.5"]},
    "tp": {"extrém": ["target.mode=htf", "target.max_rr=null"], "extrém≤4R": ["target.mode=htf", "target.max_rr=4"],
           "2R": ["target.mode=fixed_rr"]},
    "idm": {"-": ["filters.require_htf_idm=false"], "áno": ["filters.require_htf_idm=true"]},
    "okno": {"08-19": _LW_("08:00-19:00"), "LO+NY": _LW_("08:00-11:00", "14:30-17:00")},
    "mss": {"3h": ["structure.mss_max_bars=36"], "6h": ["structure.mss_max_bars=72"]},
}
GRIDS = {"ict": GRID, "ict2": GRID_ICT2, "denny": GRID_DAILY, "freq": GRID_FREQ, "market": GRID_MARKET, "poi": GRID_POI, "asia_bo": GRID_ASIA_BO, "asia_fo": GRID_ASIA_FO, "va": GRID_VA, "htf": GRID_HTF}
GRID_BASE = {"poi": POI_BASE, "asia_bo": ["strategy=asia_breakout"], "asia_fo": ["strategy=asia_fakeout"],
             "va": ["strategy=value_revert"], "htf": HTF_BASE}  # prepínače, ktoré treba už pri stavbe kontextu

_CTX = None
_BASE = None
_SPLIT = None
_GRID = None
_VERIFY = None


def overrides(combo: dict, grid: dict) -> list[str]:
    return [o for dim, label in combo.items() for o in grid[dim][label]]


def metrics(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return {"n": 0, "win%": 0.0, "PF": 0.0, "avgR": 0.0, "R": 0.0, "DD": 0.0}
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    eq = np.cumsum(r)
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())
    return {
        "n": n,
        "win%": round(100 * float((r > 0).mean()), 1),
        "PF": round(float(wins / losses), 2) if losses > 0 else 99.0,
        "avgR": round(float(r.mean()), 3),
        "R": round(float(r.sum()), 1),
        "DD": round(dd, 1),
    }


def _evaluate(combo: dict) -> dict:
    cfg = apply_overrides(_BASE, overrides(combo, _GRID))
    _CTX.cfg = cfg
    setups = engine.run(_CTX)["setups"]
    rows = [(s["signal_ns"], s["r"]) for s in setups if s.get("status") == "FILLED" and not np.isnan(s.get("r", np.nan))]
    t = np.array([x[0] for x in rows], dtype=np.int64)
    r = np.array([x[1] for x in rows], dtype=float)
    is_m = metrics(r[t < _SPLIT])
    oos_m = metrics(r[t >= _SPLIT])
    row = {**combo, **{f"IS_{k}": v for k, v in is_m.items()}, **{f"OOS_{k}": v for k, v in oos_m.items()}}
    if _VERIFY:  # overovacie obdobie (napr. 2025-07+), podmnožina OOS
        row.update({f"VER_{k}": v for k, v in metrics(r[t >= _VERIFY]).items()})
    days = {"IS": (t < _SPLIT), "OOS": (t >= _SPLIT)}
    for name, mask in days.items():  # obchody na obchodný deň (~ pracovné dni v období)
        if mask.any():
            span = (t[mask].max() - t[mask].min()) / 86_400e9 * 5 / 7
            row[f"{name}_perday"] = round(mask.sum() / max(span, 1), 2)
    return row


def run_grid(ctx, cfg: dict, split: str, grid: dict | None = None, workers: int | None = None,
             verify: str | None = None) -> pd.DataFrame:
    global _CTX, _BASE, _SPLIT, _GRID, _VERIFY
    _CTX, _BASE = ctx, cfg
    _SPLIT = pd.Timestamp(split, tz="UTC").value
    _VERIFY = pd.Timestamp(verify, tz="UTC").value if verify else None
    grid = _GRID = grid or GRID
    combos = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    workers = workers or os.cpu_count() or 1
    print(f"Testujem {len(combos)} kombinácií na {workers} jadrách, OOS od {split}", flush=True)
    out = []
    with mp.get_context("fork").Pool(workers) as pool:
        for k, row in enumerate(pool.imap_unordered(_evaluate, combos, chunksize=2), 1):
            out.append(row)
            if k % 25 == 0 or k == len(combos):
                print(f"  {k}/{len(combos)}", flush=True)
    return pd.DataFrame(out)


def rank(df: pd.DataFrame, min_is: int = 60) -> pd.DataFrame:
    """Zoradí podľa IS priemerného R (iba kombinácie s dostatkom obchodov)."""
    ok = df[df["IS_n"] >= min_is].copy()
    return ok.sort_values(["IS_avgR", "IS_PF"], ascending=False)


FEATURES = ["direction", "session", "hour_ny", "sweep_kind", "target_kind", "entry_type", "bias_source",
            "w1_aligned", "target_is_bias_dol", "strong_body", "h4_crt", "htf_poi", "vp", "inducement",
            "premium_discount", "midnight_open", "judas", "smt", "sl_bucket", "depth_bucket", "mss_bucket", "liq_rr_bucket"]


def feature_table(trades: pd.DataFrame, split: str) -> pd.DataFrame:
    """Úspešnosť a priemerné R podľa jednotlivých vlastností obchodu, zvlášť IS a OOS."""
    t = trades[trades["status"] == "FILLED"].dropna(subset=["r"]).copy()
    if not len(t):
        return pd.DataFrame()
    t["oos"] = t["signal_time"] >= pd.Timestamp(split, tz="UTC")
    t["sl_bucket"] = pd.cut(t["sl_pips"], [0, 4, 6, 9, 13, 100]).astype(str)
    t["depth_bucket"] = pd.cut(t["sweep_depth_pips"], [-1, 1, 3, 6, 100]).astype(str)
    t["mss_bucket"] = pd.cut(t["mss_bars"], [-1, 3, 8, 16, 100]).astype(str)
    t["liq_rr_bucket"] = pd.cut(t["liq_rr"], [0, 2.5, 3.5, 5, 1000]).astype(str)
    for c in ("htf_poi", "vp"):
        t[c] = t[c].fillna("").map(lambda v: bool(v))
    rows = []
    for f in FEATURES:
        for val, g in t.groupby(f):
            row = {"vlastnosť": f, "hodnota": str(val)}
            for name, part in (("IS", g[~g["oos"]]), ("OOS", g[g["oos"]])):
                m = metrics(part["r"].to_numpy())
                row.update({f"{name}_n": m["n"], f"{name}_win%": m["win%"], f"{name}_avgR": m["avgR"]})
            rows.append(row)
    return pd.DataFrame(rows)
