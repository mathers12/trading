"""Jednoduché sessionové stratégie (config `strategy`), simulované rovnako ako SMC setupy.

asia_breakout  – Londýnsky breakout ázijského rozsahu (voliteľne v smere D1/H4 biasu)
asia_fakeout   – falošné prerazenie ázijského rozsahu v Londýne (Judas / turtle soup)
value_revert   – návrat do value area predchádzajúceho dňa (pravidlo 80 %)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import simulate
from .context import Context, in_window, parse_window
from .engine import _eod_ns, effective_bias


def _direction_ok(ctx: Context, i: int, d: int, mode: str) -> bool:
    if mode == "none":
        return True
    if mode == "d1":
        return effective_bias(ctx, i)[0] == d
    if mode == "h4":
        k = ctx.m5["h4_last"][i]
        return k >= 0 and ctx.h4["bias"][k] == d
    if mode == "d1_not":  # iba dni bez jasného D1 smeru (pre návrat k hodnote)
        return ctx.d1["bias"][ctx.m5["d1_last"][i]] == 0 if ctx.m5["d1_last"][i] >= 0 else False
    raise ValueError(mode)


def _setup(ctx: Context, i: int, d: int, entry: float, sl: float, tp: float | None, rr: float, kind: str, market: bool) -> dict:
    cfg, m = ctx.cfg, ctx.m5
    t = int(m["close_ns"][i])
    risk = abs(entry - sl)
    if tp is None:
        tp = entry + d * rr * risk
    tday = m["tday"][i]
    return {
        "signal_time": pd.Timestamp(t, tz="UTC"), "signal_ns": t, "trading_day": tday.date(),
        "direction": "LONG" if d == 1 else "SHORT", "sweep_kind": kind, "entry_type": "MARKET" if market else "LIMIT",
        "entry": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5), "rr": round(abs(tp - entry) / risk, 2),
        "sl_pips": round(risk / ctx.pip, 1),
        "expiry_ns": t + int(cfg["entry"].get("expiry_minutes") or cfg["entry"]["expiry_bars"] * 5) * 60 * 10**9,
        "eod_ns": _eod_ns(tday, cfg["risk"]["eod_exit_local"], cfg["local_timezone"]),
        "hour_ny": int(m["ny_min"][i] // 60),
    }


def _local_minutes(ctx: Context) -> np.ndarray:
    lt = pd.DatetimeIndex(ctx.m5["close_ns"]).tz_localize("UTC").tz_convert(ctx.cfg["local_timezone"])
    return (lt.hour * 60 + lt.minute).to_numpy()


def _asia_ranges(ctx: Context) -> tuple[np.ndarray, np.ndarray]:
    """Pre každú M5 sviečku po skončení ázijskej seansy: high/low Ázie toho obchodného dňa (inak NaN)."""
    m = ctx.m5
    win = ctx.sessions["asia"]
    in_asia = np.array([in_window(int(x), win) for x in m["ny_min"]])
    df = pd.DataFrame({"tday": m["tday"], "h": np.where(in_asia, m["high"], np.nan), "l": np.where(in_asia, m["low"], np.nan)})
    g = df.groupby("tday")
    df["h"], df["l"] = g["h"].cummax(), g["l"].cummin()
    g = df.groupby("tday")
    hi, lo = g["h"].ffill().to_numpy(), g["l"].ffill().to_numpy()
    # Ázia (20:00–00:00 NY) je na začiatku obchodného dňa; platí až po jej skončení
    ended = ~in_asia & ~np.isnan(hi)
    return np.where(ended, hi, np.nan), np.where(ended, lo, np.nan)


def run(ctx: Context) -> dict:
    cfg, m, pip = ctx.cfg, ctx.m5, ctx.pip
    sc = cfg["session_strategy"]
    kind = cfg["strategy"]
    windows = [parse_window(w) for w in sc["windows_local"]]
    lmin = _local_minutes(ctx)
    in_win = np.array([any(in_window(int(x), w) for w in windows) for x in lmin])
    setups: list[dict] = []
    done_day = None
    busy_until = -1
    n = len(m["close_ns"])
    if kind in ("asia_breakout", "asia_fakeout"):
        ah, al = _asia_ranges(ctx)
    else:
        vp = ctx.vp
        k_vp = np.searchsorted(vp["avail_ns"], m["close_ns"], "right") - 1
        # referenčné otvorenie = prvá sviečka obchodného okna v danom dni (napr. otvorenie Londýna)
        day_open = (pd.Series(np.where(in_win, m["open"], np.nan)).groupby(m["tday"]).transform("first").to_numpy())
    ext = {}  # asia_fakeout: extrém za rozsahom v danom dni
    for i in range(n):
        tday = m["tday"][i]
        if tday == done_day or not in_win[i] or int(m["close_ns"][i]) < busy_until:
            continue
        c = m["close"][i]
        s = None
        if kind in ("asia_breakout", "asia_fakeout"):
            hi, lo = ah[i], al[i]
            if np.isnan(hi):
                continue
            rng = (hi - lo) / pip
            if not (sc["min_range_pips"] <= rng <= sc["max_range_pips"]):
                continue
            if kind == "asia_breakout":
                d = 1 if c > hi + sc["break_pips"] * pip else -1 if c < lo - sc["break_pips"] * pip else 0
                if d == 0:
                    continue
                done_day = tday  # iba prvé prerazenie v dni
                if not _direction_ok(ctx, i, d, sc["direction"]):
                    continue
                edge = hi if d == 1 else lo
                sl = {"mid": (hi + lo) / 2, "opposite": lo if d == 1 else hi,
                      "atr": c - d * sc["sl_atr"] * m["atr"][i]}[sc["sl"]]
                market = sc["entry"] == "market"
                entry = c if market else edge
                if d * (entry - sl) <= 0:
                    continue
                s = _setup(ctx, i, d, entry, sl, None, sc["rr"], "ASIA_BO", market)
            else:
                key = (tday, "ext")
                e = ext.setdefault(key, {1: np.inf, -1: -np.inf})
                e[1] = min(e[1], m["low"][i])     # najnižší low (pre long po sweepe low)
                e[-1] = max(e[-1], m["high"][i])  # najvyšší high
                for d in (-1, 1):
                    edge = hi if d == -1 else lo
                    swept = (e[-1] > hi + sc["sweep_pips"] * pip) if d == -1 else (e[1] < lo - sc["sweep_pips"] * pip)
                    back = c < hi if d == -1 else c > lo
                    if not (swept and back):
                        continue
                    done_day = tday
                    if not _direction_ok(ctx, i, d, sc["direction"]):
                        break
                    sl = e[d] - d * sc["sl_buffer_pips"] * pip
                    tp = {"opposite": lo if d == -1 else hi, "mid": (hi + lo) / 2}.get(sc["tp"])
                    market = sc["entry"] == "market"
                    entry = c if market else edge
                    risk = d * (entry - sl)
                    if risk <= 0 or not (sc["min_sl_pips"] * pip <= risk <= sc["max_sl_pips"] * pip):
                        break
                    if tp is not None and d * (tp - entry) / risk < sc["min_rr"]:
                        break
                    s = _setup(ctx, i, d, entry, sl, tp, sc["rr"], "ASIA_FO", market)
                    break
        else:  # value_revert
            k = k_vp[i]
            if k < 0:
                continue
            vah, val = vp["vah"][k], vp["val"][k]
            o = day_open[i]
            if val <= o <= vah:
                done_day = tday  # deň otvoril vnútri value area -> nič
                continue
            d = 1 if o < val else -1
            if not (val <= c <= vah):
                continue
            # potvrdenie: posledných `confirm_bars` M5 close vnútri VA
            nb = sc["confirm_bars"]
            if i - nb + 1 < 0 or m["tday"][i - nb + 1] != tday:
                continue
            cc = m["close"][i - nb + 1:i + 1]
            if not ((cc >= val) & (cc <= vah)).all():
                continue
            done_day = tday
            if not _direction_ok(ctx, i, d, sc["direction"]):
                continue
            lo_d = m["day_low"][i] if d == 1 else m["day_high"][i]
            sl = lo_d - d * sc["sl_buffer_pips"] * pip
            tp = vah if d == 1 else val
            if sc["tp"] == "poc":
                tp = vp["poc"][k]
            risk = d * (c - sl)
            if risk <= 0 or not (sc["min_sl_pips"] * pip <= risk <= sc["max_sl_pips"] * pip) or d * (tp - c) / risk < sc["min_rr"]:
                continue
            if sc["tp"] == "rr":
                tp = None
            s = _setup(ctx, i, d, c, sl, tp, sc["rr"], "VA80", True)
        if s is None:
            continue
        s.update(simulate(s, ctx.m1a))
        if s.get("exit_ns"):
            busy_until = s["exit_ns"]
        setups.append(s)
    return {"setups": setups, "rejected": [], "alerts": []}
