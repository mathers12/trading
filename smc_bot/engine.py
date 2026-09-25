"""Hľadanie setupov: sweep likvidity -> MSS s displacementom na M5 -> vstup na FVG/OB.

Long a short používajú ten istý kód: pri shorte sa ceny zrkadlia (x -> -x),
takže sweep high sa zmení na sweep low, supply na demand atď.
"""
from __future__ import annotations

from bisect import bisect_left

import numpy as np
import pandas as pd

from . import bias as bias_mod
from .backtest import simulate
from .context import DAY_NS, Context, in_window, parse_window
from .structure import find_fvgs, swing_highs, swing_lows
from .timeframes import NY

PRIORITY = ["PWH", "PWL", "PDH", "PDL", "ASIA_H", "ASIA_L", "H4_FVG", "H1_SH", "H1_SL", "H1_FVG", "M15_SH", "M15_SL", "M15_FVG"]


def _prio(kind: str) -> int:
    return PRIORITY.index(kind) if kind in PRIORITY else len(PRIORITY)


class _Side:
    """Zrkadlené polia pre jeden smer (d = +1 long, -1 short)."""

    def __init__(self, ctx: Context, d: int):
        m = ctx.m5
        self.d = d
        if d == 1:
            self.H, self.L, self.O, self.C = m["high"], m["low"], m["open"], m["close"]
        else:
            self.H, self.L, self.O, self.C = -m["low"], -m["high"], -m["open"], -m["close"]
        n = ctx.cfg["structure"]["swing_strength_m5"]
        self.sh = np.flatnonzero(swing_highs(self.H, n)).tolist()  # referencia pre MSS
        self.sl = np.flatnonzero(swing_lows(self.L, n)).tolist()  # inducement
        fv = find_fvgs(self.H, self.L, ctx.cfg["structure"]["fvg_min_atr"] * m["atr"])
        fv = fv[fv["side"] == "bull"]
        self.fvg_idx = fv["idx"].to_numpy()
        self.fvg_bot = fv["bottom"].to_numpy()
        self.fvg_top = fv["top"].to_numpy()
        lv = ctx.levels
        sweep_side = "low" if d == 1 else "high"
        ev = lv[(lv["side"] == sweep_side) & (lv["taken_idx"] >= 0) & lv["group"].isin(
            set(ctx.cfg["liquidity"]["external"]) | {"internal_fvg"})]
        self.events: dict[int, list[int]] = {}
        for lid, idx in zip(ev.index, ev["taken_idx"]):
            self.events.setdefault(int(idx), []).append(int(lid))
        target_side = "high" if d == 1 else "low"
        tg = lv[(lv["side"] == target_side) & lv["group"].isin(ctx.cfg["target"]["kinds"])]
        self.tg_price_d = d * tg["price"].to_numpy()
        self.tg_kind = tg["kind"].to_numpy()
        self.tg_group = tg["group"].to_numpy()
        self.tg_avail = tg["available_ns"].to_numpy()
        self.tg_exp = tg["expires_ns"].to_numpy()
        self.tg_taken = tg["taken_idx"].to_numpy()


def _ext_liquidity_bias(ctx: Context, i: int, price: float) -> int:
    """Bias podľa najbližšej nevybranej externej likvidity (PDH/PDL/PWH/PWL/Ázia)."""
    lv = ctx.levels
    t = ctx.m5["close_ns"][i]
    ok = (
        lv["group"].isin(["pdh_pdl", "pwh_pwl", "asia"]).to_numpy()
        & (lv["available_ns"].to_numpy() <= t)
        & (lv["expires_ns"].to_numpy() > t)
        & ((lv["taken_idx"].to_numpy() < 0) | (lv["taken_idx"].to_numpy() > i))
    )
    if not ok.any():
        return 0
    dist = np.abs(lv["price"].to_numpy()[ok] - price)
    side = lv["side"].to_numpy()[ok][int(np.argmin(dist))]
    return 1 if side == "high" else -1


def effective_bias(ctx: Context, i: int) -> tuple[int, str, int, int]:
    """(bias, zdroj, d1_bias, w1_bias). Zdroj: D1, W1, LIQ alebo ''."""
    m = ctx.m5
    k1, kw = m["d1_last"][i], m["w1_last"][i]
    d1b = int(ctx.d1["bias"][k1]) if k1 >= 1 else 0
    w1b = int(ctx.w1["bias"][kw]) if kw >= 1 else 0
    if d1b:
        return d1b, "D1", d1b, w1b
    for fb in ctx.cfg["bias"].get("fallback", []):
        if fb == "w1" and w1b:
            return w1b, "W1", d1b, w1b
        if fb == "liquidity":
            lb = _ext_liquidity_bias(ctx, i, m["close"][i])
            if lb:
                return lb, "LIQ", d1b, w1b
    return 0, "", d1b, w1b


def _h4_crt(ctx: Context, i: int, d: int) -> bool:
    """H4 CRT v smere d: sviečka vybrala high/low predošlej a zatvorila späť v rozsahu."""
    m, h4 = ctx.m5, ctx.h4
    k = m["h4_last"][i]
    pairs = []
    if k >= 1:
        pairs.append((h4["high"][k - 1], h4["low"][k - 1], h4["high"][k], h4["low"][k], h4["close"][k]))
    if k >= 0 and h4["close_ns"][k] != m["close_ns"][i]:  # rozpracovaná H4 sviečka
        pairs.append((h4["high"][k], h4["low"][k], m["h4_part_high"][i], m["h4_part_low"][i], m["close"][i]))
    for h1, l1, h2, l2, c2 in pairs:
        inside = l1 <= c2 <= h1
        if d == 1 and l2 < l1 and inside:
            return True
        if d == -1 and h2 > h1 and inside:
            return True
    return False


def _htf_poi(ctx: Context, t: int, d: int, extreme: float) -> str:
    z = ctx.zones
    if z is None or not len(z):
        return ""
    tol = ctx.cfg["poi"]["tolerance_pips"] * ctx.pip
    age = ctx.cfg["poi"]["max_age_days"] * DAY_NS
    side = "bull" if d == 1 else "bear"
    cr, inv = z["created_ns"].to_numpy(), z["invalid_ns"].to_numpy()
    ok = (
        (z["side"].to_numpy() == side)
        & (cr <= t) & (inv > t) & (cr >= t - age)
        & (z["bottom"].to_numpy() - tol <= extreme) & (extreme <= z["top"].to_numpy() + tol)
    )
    if not ok.any():
        return ""
    hits = z[ok]
    return ",".join(sorted({f"{a}_{b}" for a, b in zip(hits["tf"], hits["kind"])}))


def _vp_confluence(ctx: Context, t: int, extreme: float) -> str:
    vp = ctx.vp
    if not len(vp["avail_ns"]):
        return ""
    tol = ctx.cfg["volume_profile"]["tolerance_pips"] * ctx.pip
    k = int(np.searchsorted(vp["avail_ns"], t, "right")) - 1
    tags = []
    if k >= 0:
        for name in ("poc", "vah", "val"):
            if abs(vp[name][k] - extreme) <= tol:
                tags.append(name.upper())
        lb = ctx.cfg["volume_profile"]["npoc_lookback_days"] * DAY_NS
        ks = np.arange(0, k + 1)
        naked = (vp["touch_ns"][ks] > t) & (vp["avail_ns"][ks] >= t - lb)
        if (naked & (np.abs(vp["poc"][ks] - extreme) <= tol)).any():
            tags.append("nPOC")
    return ",".join(dict.fromkeys(tags))


def _session(ctx: Context, minute: int) -> str:
    for name, w in ctx.sessions.items():
        if in_window(minute, w):
            return name
    return "mimo"


def _eod_ns(tday: pd.Timestamp, eod_text: str, tz: str = NY) -> int:
    hh, mm = (int(x) for x in eod_text.split(":"))
    ts = pd.Timestamp(tday.year, tday.month, tday.day, hh, mm).tz_localize(tz)
    return int(ts.tz_convert("UTC").value)


def run(ctx: Context, simulate_trades: bool = True, collect_alerts: bool = False) -> dict:
    """Prejde všetky M5 sviečky a vráti setupy, zamietnuté setupy a (live) upozornenia."""
    cfg, pip, m = ctx.cfg, ctx.pip, ctx.m5
    st_cfg, en_cfg, tg_cfg, fl_cfg, rk_cfg = cfg["structure"], cfg["entry"], cfg["target"], cfg["filters"], cfg["risk"]
    nsw = st_cfg["swing_strength_m5"]
    windows = [parse_window(w) for w in fl_cfg["signal_windows_ny"]]
    killzones = [w for k, w in ctx.sessions.items() if k != "asia"]
    sides = {d: _Side(ctx, d) for d in (1, -1)}
    state: dict[int, dict | None] = {1: None, -1: None}
    setups, rejected, alerts = [], [], []
    busy_until = -1
    day_trades: dict = {}
    day_losses: dict = {}

    for i in range(len(m["close_ns"])):
        t = int(m["close_ns"][i])
        for d, sd in sides.items():
            st = state[d]
            evs = sd.events.get(i)
            if evs:
                if st is None:
                    lv = ctx.levels.loc[evs]
                    st = state[d] = {"event_idx": i, "ext_idx": i, "ext": sd.L[i], "levels": list(evs)}
                    if collect_alerts:
                        alerts.append(_armed_alert(ctx, i, d, lv))
                else:
                    st["levels"].extend(evs)
            if st is None:
                continue
            if sd.L[i] < st["ext"]:
                st["ext"], st["ext_idx"] = sd.L[i], i
            if i - st["ext_idx"] > st_cfg["mss_max_bars"]:
                state[d] = None
                continue
            pos = bisect_left(sd.sh, st["ext_idx"]) - 1
            while pos >= 0 and sd.sh[pos] + nsw > i:
                pos -= 1
            if pos < 0 or sd.sh[pos] < st["ext_idx"] - st_cfg["mss_ref_lookback"]:
                continue
            ref = sd.sh[pos]
            if sd.C[i] <= sd.H[ref]:
                continue
            # --- MSS potvrdený
            state[d] = None
            setup, reason = _build_setup(ctx, sd, st, i, ref)
            if setup is None:
                rejected.append({"time": pd.Timestamp(t, tz="UTC"), "direction": "LONG" if d == 1 else "SHORT", "reason": reason})
                continue
            reason = _filter(ctx, setup, windows, killzones)
            tkey = setup["trading_day"]
            if not reason and simulate_trades:
                if rk_cfg["one_position_at_a_time"] and t < busy_until:
                    reason = "iný obchod ešte beží"
                elif day_trades.get(tkey, 0) >= rk_cfg["max_trades_per_day"]:
                    reason = "denný limit obchodov"
                elif day_losses.get(tkey, 0) >= rk_cfg["max_losses_per_day"]:
                    reason = "denný limit strát"
            if reason:
                rejected.append({"time": setup["signal_time"], "direction": setup["direction"], "reason": reason,
                                 "entry": setup["entry"], "sl": setup["sl"], "tp": setup["tp"], "rr": setup["rr"]})
                continue
            if collect_alerts:
                alerts.append(_signal_alert(setup))
            if simulate_trades:
                res = simulate(setup, ctx.m1a)
                setup.update(res)
                if res["exit_ns"]:
                    busy_until = res["exit_ns"]
                if res["status"] == "FILLED":
                    day_trades[tkey] = day_trades.get(tkey, 0) + 1
                    if res["result"] == "SL":
                        day_losses[tkey] = day_losses.get(tkey, 0) + 1
            setups.append(setup)
    return {"setups": setups, "rejected": rejected, "alerts": alerts}


def _build_setup(ctx: Context, sd: _Side, st: dict, i: int, ref: int):
    cfg, pip, m = ctx.cfg, ctx.pip, ctx.m5
    d = sd.d
    en, tg = cfg["entry"], cfg["target"]
    ext_idx, ext = st["ext_idx"], st["ext"]
    t = int(m["close_ns"][i])
    leg = np.arange(ext_idx, i + 1)
    body = sd.C[leg] - sd.O[leg]
    strong_body = bool((body >= cfg["structure"]["displacement_body_atr"] * m["atr"][leg]).any())
    leg_fvg = bool(((sd.fvg_idx >= ext_idx + 2) & (sd.fvg_idx <= i)).any())
    disp = strong_body or leg_fvg  # displacement = silná sviečka alebo pohyb, ktorý nechal FVG
    if cfg["structure"]["require_displacement"] and not disp:
        return None, "MSS bez displacementu"

    # --- vstup: FVG vytvorený v pohybe po sweepe, inak order block
    entry_d, entry_type = None, ""
    if en["type"] in ("fvg", "fvg_or_ob"):
        k = (sd.fvg_idx >= ext_idx + 2) & (sd.fvg_idx <= i)
        best = None
        for idx, bot, top in zip(sd.fvg_idx[k], sd.fvg_bot[k], sd.fvg_top[k]):
            px = top if en["price"] == "proximal" else (top + bot) / 2
            if px >= sd.C[i] or (idx < i and sd.L[idx + 1:i + 1].min() <= px):
                continue  # cena už nad/pod vstupom alebo FVG už bol vyplnený
            if best is None or px > best:
                best = px
        if best is not None:
            entry_d, entry_type = best, "FVG"
    if entry_d is None and en["type"] in ("ob", "fvg_or_ob"):
        first_disp = leg[np.argmax(body > 0)] if (body > 0).any() else ext_idx
        for k in range(first_disp, max(ext_idx - 5, 0) - 1, -1):
            if sd.C[k] < sd.O[k]:
                top, bot = sd.H[k], sd.L[k]
                px = top if en["price"] == "proximal" else (top + bot) / 2
                if px < sd.C[i] and (k >= i or sd.L[k + 1:i + 1].min() > px or k + 1 > i):
                    entry_d, entry_type = px, "OB"
                break
    if en["type"] == "ote":
        # ICT OTE: 70,5 % spätného pohybu displacementu (extrém sweepu -> vrchol pohybu)
        leg_high = sd.H[ext_idx:i + 1].max()
        px = leg_high - en.get("ote_level", 0.705) * (leg_high - ext)
        if px < sd.C[i]:
            entry_d, entry_type = px, "OTE"
    if entry_d is None:
        return None, "žiadny FVG/OB na vstup"

    sl_d = ext - en["sl_buffer_pips"] * pip
    risk_d = entry_d - sl_d
    sl_pips = risk_d / pip
    if sl_pips < en["min_sl_pips"]:
        return None, f"SL príliš malý ({sl_pips:.1f} pip)"
    if sl_pips > en["max_sl_pips"]:
        return None, f"SL príliš veľký ({sl_pips:.1f} pip)"

    # --- cieľ: najbližšia opačná likvidita s RR >= min
    ok = (sd.tg_avail <= t) & (sd.tg_exp > t) & ((sd.tg_taken < 0) | (sd.tg_taken > i)) & (sd.tg_price_d > entry_d)
    if not ok.any():
        return None, "žiadna opačná likvidita"
    order = np.flatnonzero(ok)[np.argsort(sd.tg_price_d[ok], kind="stable")]
    rr_all = (sd.tg_price_d[order] - entry_d) / risk_d
    mode = tg["mode"]
    if mode == "nearest":
        pick = 0 if rr_all[0] >= tg["min_rr"] else -1
    else:
        hits = np.flatnonzero(rr_all >= tg["min_rr"])
        pick = int(hits[0]) if len(hits) else -1
    if mode == "fixed_rr":
        pick = 0  # TP pevne na min_rr, likvidita slúži iba na popis
    if pick < 0:
        return None, f"RR pod {tg['min_rr']} (najbližšia likvidita {rr_all[0]:.2f}R)"
    tk = order[pick]
    tp_d, rr = sd.tg_price_d[tk], float(rr_all[pick])
    if mode in ("fixed_rr", "fixed_rr_liq"):
        # fixed_rr_liq: TP na min_rr, ale iba keď je za ním likvidita (magnet)
        tp_d, rr = entry_d + tg["min_rr"] * risk_d, float(tg["min_rr"])
    elif tg.get("max_rr") and rr > tg["max_rr"]:
        tp_d, rr = entry_d + tg["max_rr"] * risk_d, float(tg["max_rr"])

    # --- konfluencie (tagy)
    b, bsrc, d1b, w1b = effective_bias(ctx, i)
    extreme = d * ext
    lv = ctx.levels.loc[st["levels"]]
    primary = min(lv["kind"], key=_prio)
    sweep_price = float(lv.loc[lv["kind"] == primary, "price"].iloc[0])
    lb = cfg["inducement"]["lookback_bars"]
    ev = st["event_idx"]
    level_d = d * sweep_price
    idm = False
    for j in sd.sl[bisect_left(sd.sl, ev - lb):bisect_left(sd.sl, ev)]:
        if level_d < sd.L[j] <= level_d + cfg["inducement"]["max_distance_pips"] * pip and j + 2 <= ev:
            idm = True
            break
    k1 = m["d1_last"][i]
    pdh = ctx.d1["high"][k1] if k1 >= 0 else m["day_high"][i]
    pdl = ctx.d1["low"][k1] if k1 >= 0 else m["day_low"][i]
    mid = (max(pdh, m["day_high"][i]) + min(pdl, m["day_low"][i])) / 2
    entry = d * entry_d
    pd_ok = entry < mid if d == 1 else entry > mid
    tday = m["tday"][i]
    signal_time = pd.Timestamp(t, tz="UTC")
    setup = {
        "signal_time": signal_time,
        "signal_ns": t,
        "trading_day": tday.date(),
        "direction": "LONG" if d == 1 else "SHORT",
        "sweep_kind": primary,
        "swept": ",".join(dict.fromkeys(lv["kind"])),
        "sweep_level": round(sweep_price, 5),
        "sweep_time": pd.Timestamp(int(m["close_ns"][ev]), tz="UTC"),
        "extreme": round(extreme, 5),
        "mss_level": round(d * sd.H[ref], 5),
        "entry_type": entry_type,
        "entry": round(entry, 5),
        "sl": round(d * sl_d, 5),
        "tp": round(d * tp_d, 5),
        "target_kind": sd.tg_kind[tk],
        "rr": round(rr, 2),
        "sl_pips": round(sl_pips, 1),
        "expiry_ns": t + cfg["entry"]["expiry_bars"] * 5 * 60 * 10**9,
        "eod_ns": (_eod_ns(tday, cfg["risk"]["eod_exit_local"], cfg["local_timezone"]) if cfg["risk"].get("eod_exit_local")
                   else _eod_ns(tday, cfg["risk"]["eod_exit_ny"])),
        "session": _session(ctx, int(m["ny_min"][i])),
        "bias": bias_mod.label(b),
        "bias_source": bsrc,
        "d1_bias": bias_mod.label(d1b),
        "w1_bias": bias_mod.label(w1b),
        "bias_aligned": b == d,
        "w1_aligned": w1b == d,
        "target_is_bias_dol": bool(b == d and sd.tg_group[tk] in ("pdh_pdl", "pwh_pwl")),
        "displacement": disp,
        "strong_body": strong_body,
        "h4_crt": _h4_crt(ctx, i, d),
        "htf_poi": _htf_poi(ctx, t, d, extreme),
        "vp": _vp_confluence(ctx, t, extreme),
        "inducement": idm,
        "premium_discount": bool(pd_ok),
        "hour_ny": int(m["ny_min"][i] // 60),
        "sweep_depth_pips": round((level_d - ext) / pip, 1),
        "mss_bars": int(i - ext_idx),
        "liq_rr": round(float(rr_all[max(pick, 0)]), 2),
        "midnight_open": bool(not np.isnan(m["midnight_open"][i]) and (
            entry < m["midnight_open"][i] if d == 1 else entry > m["midnight_open"][i])),
        "judas": bool(primary.startswith("ASIA") and in_window(int(m["ny_min"][i]), ctx.sessions["london"])),
    }
    return setup, ""


def _filter(ctx: Context, s: dict, windows, killzones) -> str:
    cfg = ctx.cfg
    fl = cfg["filters"]
    minute = int(s["signal_time"].tz_convert(NY).hour * 60 + s["signal_time"].tz_convert(NY).minute)
    if s["signal_ns"] >= s["eod_ns"]:
        return "po konci obchodného dňa"
    if windows and not any(in_window(minute, w) for w in windows):
        return "mimo povoleného času"
    local_windows = fl.get("signal_windows_local")
    if local_windows:
        lt = s["signal_time"].tz_convert(cfg["local_timezone"])
        if not any(in_window(lt.hour * 60 + lt.minute, parse_window(w)) for w in local_windows):
            return "mimo obchodného času"
    if fl["require_killzone"] and not any(in_window(minute, w) for w in killzones):
        return "mimo killzone"
    if fl.get("sweep_kinds") and s["sweep_kind"] not in fl["sweep_kinds"]:
        return f"likvidita {s['sweep_kind']} nie je povolená"
    if cfg["bias"]["filter"] and not s["bias_aligned"]:
        return f"proti biasu ({s['bias']})"
    for key, name in (("require_htf_poi", "htf_poi"), ("require_h4_crt", "h4_crt"), ("require_inducement", "inducement"),
                      ("require_vp", "vp"), ("require_premium_discount", "premium_discount"),
                      ("require_midnight_open", "midnight_open")):
        if fl.get(key) and not s[name]:
            return f"chýba {name}"
    return ""


def _armed_alert(ctx: Context, i: int, d: int, lv: pd.DataFrame) -> dict:
    m = ctx.m5
    t = int(m["close_ns"][i])
    windows = [parse_window(w) for w in ctx.cfg["filters"]["signal_windows_ny"]]
    b, bsrc, _, _ = effective_bias(ctx, i)
    kind = min(lv["kind"], key=_prio)
    price = float(lv.loc[lv["kind"] == kind, "price"].iloc[0])
    return {
        "ns": t, "stage": "armed", "direction": "LONG" if d == 1 else "SHORT", "bias_aligned": b == d,
        "in_window": not windows or any(in_window(int(m["ny_min"][i]), w) for w in windows),
        "session": _session(ctx, int(m["ny_min"][i])),
        "text": (f"🟡 {ctx.cfg['instrument']} – sweep {kind} ({price:.5f}). "
                 f"Bias: {bias_mod.label(b)}{' (' + bsrc + ')' if bsrc else ''}. "
                 f"Čakám na MSS na M5 pre {'LONG' if d == 1 else 'SHORT'}."),
    }


def _signal_alert(s: dict) -> dict:
    tags = [x for x in (
        "H4 CRT" if s["h4_crt"] else "", f"POI {s['htf_poi']}" if s["htf_poi"] else "", f"VP {s['vp']}" if s["vp"] else "",
        "inducement" if s["inducement"] else "", "premium/discount" if s["premium_discount"] else "") if x]
    return {
        "ns": s["signal_ns"], "stage": "signal", "direction": s["direction"], "bias_aligned": s["bias_aligned"],
        "session": s["session"],
        "text": (f"🟢 {s['direction']} setup – sweep {s['sweep_kind']} → MSS s displacementom\n"
                 f"Vstup ({s['entry_type']}): {s['entry']:.5f}\nSL: {s['sl']:.5f} ({s['sl_pips']} pip)\n"
                 f"TP: {s['tp']:.5f} ({s['target_kind']}) | RR 1:{s['rr']}\n"
                 f"Bias: {s['bias']} ({s['bias_source'] or '-'}) | Seansa: {s['session']}\n"
                 f"Konfluencie: {', '.join(tags) if tags else '-'}"),
    }
