"""Simulácia jedného obchodu na M1 bid/ask dátach.

Konzervatívne pravidlá:
  * long sa plní za ask, zatvára za bid (short naopak) -> spread je zahrnutý
  * ak v tej istej M1 sviečke padne SL aj TP, počíta sa SL
  * v sviečke, v ktorej sa limitka naplnila, sa TP nepočíta (nevieme poradie)
  * ak cena dosiahne TP skôr, ako sa limitka naplní -> MISSED
  * o eod_exit (NY) sa otvorený obchod zatvorí, nenaplnená limitka zruší
"""
from __future__ import annotations

import numpy as np


def simulate(setup: dict, m1a: dict) -> dict:
    long = setup["direction"] == "LONG"
    entry, sl, tp = setup["entry"], setup["sl"], setup["tp"]
    risk = abs(entry - sl)
    open_ns = m1a["open_ns"]
    start = int(np.searchsorted(open_ns, setup["signal_ns"], "left"))
    expiry, eod = setup["expiry_ns"], setup["eod_ns"]
    # long: vstup/SL/TP podľa ask pri vstupe a bid pri výstupe
    e_low, e_high = (m1a["ask_low"], m1a["ask_high"]) if long else (m1a["bid_low"], m1a["bid_high"])
    x_open, x_low, x_high = (
        (m1a["bid_open"], m1a["bid_low"], m1a["bid_high"]) if long else (m1a["ask_open"], m1a["ask_low"], m1a["ask_high"])
    )
    out = {"status": "", "result": "", "fill_ns": None, "exit_ns": None, "exit_price": np.nan, "r": np.nan}

    if setup.get("entry_type") == "MARKET":
        return _simulate_market(setup, m1a, long, start, x_open, x_low, x_high, out)
    fill = -1
    j = start
    n = len(open_ns)
    while j < n:
        t = open_ns[j]
        if t >= eod:
            out.update(status="CANCELLED_EOD", exit_ns=int(t))
            return out
        if t >= expiry:
            out.update(status="NOT_FILLED", exit_ns=int(t))
            return out
        filled = e_low[j] <= entry if long else e_high[j] >= entry
        if filled:
            fill = j
            break
        tp_first = x_high[j] >= tp if long else x_low[j] <= tp
        if tp_first:
            out.update(status="MISSED", exit_ns=int(t))
            return out
        j += 1
    if fill < 0:
        out.update(status="NO_DATA")
        return out

    out.update(status="FILLED", fill_ns=int(open_ns[fill]))
    for j in range(fill, n):
        t = open_ns[j]
        if j > fill and t >= eod:
            px = x_open[j]
            out.update(result="EOD", exit_ns=int(t), exit_price=px)
            break
        sl_hit = x_low[j] <= sl if long else x_high[j] >= sl
        if sl_hit:
            gap = x_open[j] <= sl if long else x_open[j] >= sl
            px = x_open[j] if (gap and j > fill) else sl
            out.update(result="SL", exit_ns=int(t) + 60 * 10**9, exit_price=px)
            break
        if j > fill:
            tp_hit = x_high[j] >= tp if long else x_low[j] <= tp
            if tp_hit:
                out.update(result="TP", exit_ns=int(t) + 60 * 10**9, exit_price=tp)
                break
    else:
        out.update(result="OPEN")
        return out
    move = out["exit_price"] - entry if long else entry - out["exit_price"]
    out["r"] = move / risk
    return out


def _simulate_market(setup, m1a, long, start, x_open, x_low, x_high, out):
    """Trhový vstup na otvorení prvej M1 sviečky po signáli (long za ask, short za bid)."""
    n = len(m1a["open_ns"])
    if start >= n or m1a["open_ns"][start] >= setup["eod_ns"]:
        out.update(status="CANCELLED_EOD")
        return out
    fill_px = m1a["ask_open"][start] if long else m1a["bid_open"][start]
    sl = setup["sl"]
    risk = (fill_px - sl) if long else (sl - fill_px)
    if risk <= 0:
        out.update(status="NOT_FILLED", exit_ns=int(m1a["open_ns"][start]))
        return out
    tp = fill_px + setup["rr"] * risk if long else fill_px - setup["rr"] * risk
    setup["entry"], setup["tp"] = round(float(fill_px), 5), round(float(tp), 5)
    out.update(status="FILLED", fill_ns=int(m1a["open_ns"][start]))
    for j in range(start, n):
        t = m1a["open_ns"][j]
        if j > start and t >= setup["eod_ns"]:
            out.update(result="EOD", exit_ns=int(t), exit_price=x_open[j])
            break
        if (x_low[j] <= sl) if long else (x_high[j] >= sl):
            out.update(result="SL", exit_ns=int(t) + 60 * 10**9, exit_price=sl)
            break
        if (x_high[j] >= tp) if long else (x_low[j] <= tp):
            out.update(result="TP", exit_ns=int(t) + 60 * 10**9, exit_price=tp)
            break
    else:
        out.update(result="OPEN")
        return out
    move = out["exit_price"] - fill_px if long else fill_px - out["exit_price"]
    out["r"] = move / risk
    return out
