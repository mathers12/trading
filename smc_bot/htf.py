"""HTF pullback model (podľa používateľovho dokumentu trading_patterns):

1. štruktúra vyššieho TF (H1/H4): BOS close-om za posledným swingom určí smer a rozsah impulzu (lo → hi),
2. pullback do golden pocketu (fib_min – fib_max rozsahu), v ňom HTF FVG z impulzu = POI,
3. inducement = LTF swing, ktorý vznikol počas pullbacku („likvidita napravo“) a cena ho pri vstupe do POI vybrala,
4. potom LTF MSS s displacementom a vstup (rieši engine), TP voliteľne na extréme impulzu (hi).

Udalosť (prvý vstup do POI v danom rozsahu) sa pridáva do ctx.levels ako skupina `htf_pb`,
takže engine ju spracuje rovnako ako sweep likvidity. Všetko je známe až po uzavretí sviečok.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .structure import atr, find_fvgs, swing_highs, swing_lows
from .timeframes import NEVER, to_ns


def _structure(h, l, c, n):
    """Bull štruktúra na (zrkadlených) poliach: pre každú sviečku k stav po jej uzavretí.

    Vracia trend (1 = po bull BOS, 0 = nič), lo, hi, lo_idx, hi_idx, bos_idx.
    """
    m = len(h)
    sh = swing_highs(h, n)
    trend = np.zeros(m, dtype=np.int8)
    lo_a, hi_a = np.full(m, np.nan), np.full(m, np.nan)
    lo_i, hi_i, bos_a = np.full(m, -1), np.full(m, -1), np.full(m, -1)
    last_sh, last_sh_idx, broken = np.nan, -1, True
    tr, lo, hi, li, hix, bos = 0, np.nan, np.nan, -1, -1, -1
    for k in range(m):
        j = k - n
        if j >= 0 and sh[j]:
            last_sh, last_sh_idx, broken = h[j], j, False
        if not broken and c[k] > last_sh:
            broken = True
            seg = l[last_sh_idx:k + 1]
            li = last_sh_idx + int(np.argmin(seg))
            tr, lo, bos = 1, float(seg.min()), k
            hix = li + int(np.argmax(h[li:k + 1]))
            hi = float(h[hix])
        elif tr == 1:
            if h[k] > hi:
                hi, hix = float(h[k]), k
            if c[k] < lo:
                tr = 0
        trend[k], lo_a[k], hi_a[k], lo_i[k], hi_i[k], bos_a[k] = tr, lo, hi, li, hix, bos
    return trend, lo_a, hi_a, lo_i, hi_i, bos_a


def tf_direction(df: pd.DataFrame, n: int) -> np.ndarray:
    """Smer štruktúry po uzavretí každej sviečky: smer posledného BOS (+1 bull, -1 bear, 0 zatiaľ žiadny)."""
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    bull = _structure(h, l, c, n)[5]
    bear = _structure(-l, -h, -c, n)[5]
    return np.where((bull < 0) & (bear < 0), 0, np.where(bull > bear, 1, -1)).astype(np.int8)


ORDER = ["W1", "D1", "H4", "H1", "M15", "M5"]


def aligned(ctx, t5: np.ndarray, d: int, below: str | None = None) -> np.ndarray:
    """Top-down: smer d súhlasí so štruktúrou všetkých TF z htf.align (napr. W1, D1, H4) – posledné uzavreté sviečky."""
    ok = np.ones(len(t5), dtype=bool)
    n = int(ctx.cfg["htf"]["swing_strength"])
    for tf in ctx.cfg["htf"].get("align") or []:
        if below and ORDER.index(tf) >= ORDER.index(below):
            continue  # zosúladenie iba s vyššími TF, než je TF pullbacku
        if tf in ("W1", "D1"):  # bias z dvoch posledných sviečok (pravidlo používateľa, bias.py) – stačí krátka história
            arr = ctx.w1 if tf == "W1" else ctx.d1
            k = np.searchsorted(arr["close_ns"], t5, "right") - 1
            ok &= (k >= 0) & (arr["bias"][np.maximum(k, 0)] == d)
            continue
        df = ctx.frames[tf]
        dirs = tf_direction(df, n)
        k = np.searchsorted(to_ns(df["close_time"]), t5, "right") - 1
        ok &= (k >= 0) & (dirs[np.maximum(k, 0)] == d)
    return ok


def events(ctx, d: int, tf_pb: str) -> list[tuple]:
    """Udalosti pre smer d: (m5_idx, cena_dotyku, far=lo, tp=hi, kind, idm) v reálnych cenách."""
    cfg = ctx.cfg
    hc = cfg["htf"]
    f = ctx.frames
    H = f[tf_pb]
    sg = 1 if d == 1 else -1
    if d == 1:
        hh, hl, hcl = H["high"].to_numpy(), H["low"].to_numpy(), H["close"].to_numpy()
        L, Hm = ctx.m5["low"], ctx.m5["high"]
    else:
        hh, hl, hcl = -H["low"].to_numpy(), -H["high"].to_numpy(), -H["close"].to_numpy()
        L, Hm = -ctx.m5["high"], -ctx.m5["low"]
    trend, lo, hi, lo_i, hi_i, bos = _structure(hh, hl, hcl, int(hc["swing_strength"]))
    h_close = to_ns(H["close_time"])
    h_atr = atr(hh, hl, hcl, cfg["structure"]["atr_period"])  # sila impulzu v násobkoch ATR pullback TF
    h_open = to_ns(H.index)

    # POI: bull FVG na POI TF (zrkadlené; H1 aj M15), platný kým close nepadne pod spodok
    tfs = hc["poi_timeframe"]
    tfs = [tfs] if isinstance(tfs, str) else list(tfs)
    cr_l, bot_l, top_l, inv_l, tf_l = [], [], [], [], []
    for tf in tfs:
        P = f[tf]
        if d == 1:
            ph, pl, pc = P["high"].to_numpy(), P["low"].to_numpy(), P["close"].to_numpy()
        else:
            ph, pl, pc = -P["low"].to_numpy(), -P["high"].to_numpy(), -P["close"].to_numpy()
        a = atr(ph, pl, pc, cfg["structure"]["atr_period"])
        fv = find_fvgs(ph, pl, cfg["structure"]["fvg_min_atr"] * a)
        fv = fv[fv["side"] == "bull"]
        p_close = to_ns(P["close_time"])
        for idx, bot, top in zip(fv["idx"].to_numpy(), fv["bottom"].to_numpy(), fv["top"].to_numpy()):
            hit = pc[idx + 1:] < bot
            inv_l.append(p_close[idx + 1 + int(np.argmax(hit))] if hit.any() else NEVER)
            cr_l.append(p_close[idx]); bot_l.append(bot); top_l.append(top); tf_l.append(tf)
    fv_cr, fv_bot, fv_top = np.array(cr_l, dtype=np.int64), np.array(bot_l), np.array(top_l)
    fv_inv, fv_tf = np.array(inv_l, dtype=np.int64), np.array(tf_l)

    m = ctx.m5
    t5 = m["close_ns"]
    kidx = np.searchsorted(h_close, t5, "right") - 1  # posledná uzavretá HTF sviečka
    kidx = np.where(aligned(ctx, t5, d, tf_pb), kidx, -1)  # top-down filter (W1 -> D1 -> H4)
    nsw = cfg["structure"]["swing_strength_m5"]
    ltf_sl = np.flatnonzero(swing_lows(L, nsw))
    f_min, f_max = hc["fib_min"], hc["fib_max"]
    touches: dict[int, int] = {}  # počet vstupov do zóny v danom impulze (bos)
    left: dict[int, bool] = {}  # cena od posledného vstupu opustila zónu
    max_t = int(hc.get("max_touches", 1))
    out = []
    for i in np.flatnonzero((kidx >= 0)):
        k = kidx[i]
        if trend[k] != 1 or touches.get(bos[k], 0) >= max_t:
            continue
        R = hi[k] - lo[k]
        if R <= 0 or R < hc.get("min_impulse_atr", 0) * h_atr[k]:
            continue  # malý impulz = šum/bočný trh
        top_z = hi[k] - f_min * R  # začiatok golden pocketu
        bot_z = hi[k] - f_max * R
        if L[i] > top_z:
            left[bos[k]] = True
            continue
        if L[i] < lo[k] or not left.get(bos[k], True):
            continue
        t = int(t5[i])
        leg_start = int(h_open[lo_i[k]])
        poi = ""
        if hc["require_fvg"]:
            ok = (fv_cr <= t) & (fv_inv > t) & (fv_cr >= leg_start) & (fv_bot <= top_z) & (fv_top >= bot_z) & (L[i] <= fv_top)
            if not ok.any():
                continue
            poi = f"{fv_tf[np.flatnonzero(ok)[0]]}_FVG"
        touches[bos[k]] = touches.get(bos[k], 0) + 1
        left[bos[k]] = False
        # inducement: LTF swing low potvrdený počas pullbacku (po vrchole impulzu), nad zónou -> teraz vybratý
        hi_t = int(h_open[hi_i[k]])
        js = ltf_sl[(ltf_sl + nsw < i)]
        js = js[(t5[js] >= hi_t)]
        # prísny inducement: swing low najviac idm_max_pips nad zónou (likvidita tesne pred POI)
        near = top_z + hc.get("idm_max_pips", 1e9) * ctx.pip
        idm = bool(((L[js] > top_z) & (L[js] <= near) & (L[js] > L[i])).any()) if len(js) else False
        out.append((int(i), sg * float(min(L[i], top_z)), sg * float(lo[k]), sg * float(hi[k]),
                    f"{tf_pb}_PB" + ("_" + poi if poi else ""), idm))
    return out


def level_rows(ctx) -> pd.DataFrame:
    rows = []
    tfs = ctx.cfg["htf"]["timeframe"]
    for tf_pb in ([tfs] if isinstance(tfs, str) else tfs):  # pullback naraz na viacerých TF (napr. H1, M15, M5)
        for d in (1, -1):
            for i, price, far, tp, kind, idm in events(ctx, d, tf_pb):
                rows.append({"price": price, "side": "low" if d == 1 else "high", "kind": kind, "group": "htf_pb",
                             "available_ns": int(ctx.m5["close_ns"][i]), "expires_ns": NEVER, "taken_idx": i,
                             "far": far, "tp": tp, "idm": idm})
    return pd.DataFrame(rows)
