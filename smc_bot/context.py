"""Predpočítanie všetkého, čo stratégia potrebuje: timeframy, likvidita, POI, bias, VP.

Každá informácia má čas, odkedy je známa (available / created / close_time),
takže backtest nikdy nenazerá do budúcnosti.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import bias as bias_mod
from .structure import atr, find_fvgs, find_order_blocks, swing_highs, swing_lows
from .timeframes import FREQ, NEVER, ny_minutes, period_key, resample, to_ns, trading_day
from .volume_profile import daily_profiles, naked_poc_touch_times

DAY_NS = 86_400 * 10**9


def parse_window(text: str) -> tuple[int, int]:
    a, b = text.split("-")
    to_min = lambda s: int(s.split(":")[0]) * 60 + int(s.split(":")[1])  # noqa: E731
    return to_min(a), to_min(b)


def in_window(minute: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


@dataclass
class Context:
    cfg: dict
    pip: float
    m1: pd.DataFrame
    frames: dict
    m5: dict = field(default_factory=dict)
    m1a: dict = field(default_factory=dict)
    d1: dict = field(default_factory=dict)
    w1: dict = field(default_factory=dict)
    h4: dict = field(default_factory=dict)
    levels: pd.DataFrame | None = None
    zones: pd.DataFrame | None = None
    vp: dict = field(default_factory=dict)
    sessions: dict = field(default_factory=dict)


def _first_cross(open_ns, arr, start_ns, end_ns, price, above: bool) -> int:
    s = np.searchsorted(open_ns, start_ns, "left")
    e = np.searchsorted(open_ns, end_ns, "left") if end_ns != NEVER else len(open_ns)
    if e <= s:
        return -1
    seg = arr[s:e] > price if above else arr[s:e] < price
    return int(s + np.argmax(seg)) if seg.any() else -1


def _zones_for_tf(df: pd.DataFrame, tf: str, cfg: dict) -> pd.DataFrame:
    h, l, o, c = (df[k].to_numpy() for k in ("high", "low", "open", "close"))
    a = atr(h, l, c, cfg["structure"]["atr_period"])
    fv = find_fvgs(h, l, cfg["structure"]["fvg_min_atr"] * a)
    ob = find_order_blocks(o, h, l, c, fv)
    fv["kind"], ob["kind"] = "FVG", "OB"
    z = pd.concat([fv, ob], ignore_index=True)
    close_ns = to_ns(df["close_time"])
    z["tf"] = tf
    z["created_ns"] = close_ns[z["idx"].to_numpy()] if len(z) else np.array([], dtype=np.int64)
    inval = np.full(len(z), NEVER, dtype=np.int64)
    for k, (idx, side, bot, top) in enumerate(zip(z["idx"], z["side"], z["bottom"], z["top"])):
        seg = c[idx + 1:]
        hit = seg < bot if side == "bull" else seg > top
        if hit.any():
            inval[k] = close_ns[idx + 1 + int(np.argmax(hit))]
    z["invalid_ns"] = inval
    return z


def build_context(m1: pd.DataFrame, cfg: dict) -> Context:
    pip = float(cfg["pip"])
    frames = {tf: resample(m1, tf) for tf in ("M5", "M15", "H1", "H4", "D1", "W1")}
    ctx = Context(cfg=cfg, pip=pip, m1=m1, frames=frames)
    ctx.sessions = {k: parse_window(v) for k, v in cfg["sessions"].items()}

    # --- M1 (bid/ask na simuláciu obchodov)
    ctx.m1a = {"open_ns": to_ns(m1.index)}
    for side in ("bid", "ask"):
        for k in ("open", "high", "low", "close"):
            ctx.m1a[f"{side}_{k}"] = m1[f"{side}_{k}"].to_numpy()

    # --- M5 (hlavný timeframe setupu)
    m5 = frames["M5"]
    h, l, o, c = (m5[k].to_numpy() for k in ("high", "low", "open", "close"))
    close_ns = to_ns(m5["close_time"])
    tday = trading_day(m5.index)
    h4_key = period_key(m5.index, "H4")
    ctx.m5 = {
        "open_ns": to_ns(m5.index),
        "close_ns": close_ns,
        "open": o, "high": h, "low": l, "close": c,
        "atr": atr(h, l, c, cfg["structure"]["atr_period"]),
        "tday": tday,
        "ny_min": ny_minutes(m5.index),
        "h4_part_high": pd.Series(h).groupby(h4_key.to_numpy()).cummax().to_numpy(),
        "h4_part_low": pd.Series(l).groupby(h4_key.to_numpy()).cummin().to_numpy(),
        "day_high": pd.Series(h).groupby(tday.to_numpy()).cummax().to_numpy(),
        "day_low": pd.Series(l).groupby(tday.to_numpy()).cummin().to_numpy(),
    }

    # --- D1 / W1 / H4 + bias
    for name, tf in (("d1", "D1"), ("w1", "W1"), ("h4", "H4")):
        df = frames[tf]
        arr = {k: df[k].to_numpy() for k in ("open", "high", "low", "close")}
        arr["close_ns"] = to_ns(df["close_time"])
        arr["bias"] = bias_mod.bias_series(arr["high"], arr["low"], arr["close"])
        setattr(ctx, name, arr)
    ctx.m5["d1_last"] = np.searchsorted(ctx.d1["close_ns"], close_ns, "right") - 1
    ctx.m5["w1_last"] = np.searchsorted(ctx.w1["close_ns"], close_ns, "right") - 1
    ctx.m5["h4_last"] = np.searchsorted(ctx.h4["close_ns"], close_ns, "right") - 1

    ctx.levels = _build_levels(ctx)
    zones = [_zones_for_tf(frames[tf], tf, cfg) for tf in sorted(set(cfg["poi"]["timeframes"]))]
    ctx.zones = pd.concat(zones, ignore_index=True) if zones else pd.DataFrame()

    # --- Volume profile
    vpc = cfg["volume_profile"]
    prof = daily_profiles(m1, trading_day(m1.index), frames["D1"], vpc["bin_pips"] * pip, vpc["value_area"])
    ctx.vp = {
        "avail_ns": to_ns(prof["available_at"]) if len(prof) else np.array([], dtype=np.int64),
        "poc": prof["poc"].to_numpy(),
        "vah": prof["vah"].to_numpy(),
        "val": prof["val"].to_numpy(),
        "touch_ns": naked_poc_touch_times(prof, m5) if len(prof) else np.array([], dtype=np.int64),
    }
    return ctx


def _build_levels(ctx: Context) -> pd.DataFrame:
    """Tabuľka likvidity: každá úroveň má kedy vznikla, kedy expiruje a kedy ju cena vybrala."""
    cfg, f = ctx.cfg, ctx.frames
    lq = cfg["liquidity"]
    groups = set(lq["external"]) | set(cfg["target"]["kinds"])
    max_age = int(lq["max_age_days"] * DAY_NS)
    rows: list[tuple] = []  # price, side, kind, group, available_ns, expires_ns

    def period_levels(tf, group, kh, kl):
        df = f[tf]
        cn = to_ns(df["close_time"])
        for k in range(len(df)):
            exp = cn[k + 1] if k + 1 < len(df) else NEVER
            rows.append((df["high"].iat[k], "high", kh, group, cn[k], exp))
            rows.append((df["low"].iat[k], "low", kl, group, cn[k], exp))

    if "pdh_pdl" in groups:
        period_levels("D1", "pdh_pdl", "PDH", "PDL")
    if "pwh_pwl" in groups:
        period_levels("W1", "pwh_pwl", "PWH", "PWL")
    if "asia" in groups:
        m5 = ctx.m5
        win = ctx.sessions["asia"]
        mask = np.array([in_window(int(x), win) for x in m5["ny_min"]])
        d1_close = dict(zip(f["D1"]["period"], to_ns(f["D1"]["close_time"])))
        df = pd.DataFrame({"tday": m5["tday"][mask], "h": m5["high"][mask], "l": m5["low"][mask], "cn": m5["close_ns"][mask]})
        for day, g in df.groupby("tday"):
            exp = d1_close.get(day, NEVER)
            avail = int(g["cn"].max())
            rows.append((g["h"].max(), "high", "ASIA_H", "asia", avail, exp))
            rows.append((g["l"].min(), "low", "ASIA_L", "asia", avail, exp))
    n = int(lq["swing_strength"])
    for tf, group in (("H1", "h1_swings"), ("M15", "m15_swings")):
        if group not in groups:
            continue
        df = f[tf]
        cn = to_ns(df["close_time"])
        hh, ll = df["high"].to_numpy(), df["low"].to_numpy()
        for side, flags, arr, kind in (("high", swing_highs(hh, n), hh, f"{tf}_SH"), ("low", swing_lows(ll, n), ll, f"{tf}_SL")):
            for i in np.flatnonzero(flags):
                if i + n < len(df):
                    rows.append((arr[i], side, kind, group, cn[i + n], cn[i + n] + max_age))

    # vnútorná likvidita: dotyk HTF FVG
    for tf in lq["internal_fvg_timeframes"]:
        z = _zones_for_tf(f[tf], tf, cfg)
        z = z[z["kind"] == "FVG"]
        for side, bot, top, cr, inv in zip(z["side"], z["bottom"], z["top"], z["created_ns"], z["invalid_ns"]):
            # bear FVG nad cenou -> short event pri dotyku spodnej hrany
            if side == "bear":
                rows.append((bot, "high", f"{tf}_FVG", "internal_fvg", cr, min(inv, cr + max_age)))
            else:
                rows.append((top, "low", f"{tf}_FVG", "internal_fvg", cr, min(inv, cr + max_age)))

    lv = pd.DataFrame(rows, columns=["price", "side", "kind", "group", "available_ns", "expires_ns"])
    m5 = ctx.m5
    taken = np.empty(len(lv), dtype=np.int64)
    for k, (p, s, a, e) in enumerate(zip(lv["price"].to_numpy(), lv["side"].to_numpy(), lv["available_ns"].to_numpy(), lv["expires_ns"].to_numpy())):
        arr = m5["high"] if s == "high" else m5["low"]
        taken[k] = _first_cross(m5["open_ns"], arr, int(a), int(e), p, above=(s == "high"))
    lv["taken_idx"] = taken
    return lv
