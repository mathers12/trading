"""Denný volume profile z M1 dát: POC, VAH, VAL a naked POC.

Pozor: forex nemá centrálnu burzu, OANDA `volume` je tick volume (počet zmien ceny).
Profil je preto aproximácia skutočného objemu.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .timeframes import NEVER, to_ns


def _profile(low: np.ndarray, high: np.ndarray, vol: np.ndarray, bin_size: float, va_share: float):
    lo = np.floor(low / bin_size).astype(np.int64)
    hi = np.floor(high / bin_size).astype(np.int64)
    base = lo.min()
    size = hi.max() - base + 2
    per_bin = vol / (hi - lo + 1)
    diff = np.zeros(size)
    np.add.at(diff, lo - base, per_bin)
    np.add.at(diff, hi - base + 1, -per_bin)
    hist = np.cumsum(diff)[:-1]
    total = hist.sum()
    poc = int(np.argmax(hist))
    a = b = poc
    acc = hist[poc]
    while acc < va_share * total and (a > 0 or b < len(hist) - 1):
        up = hist[b + 1] if b < len(hist) - 1 else -1
        down = hist[a - 1] if a > 0 else -1
        if up >= down:
            b += 1
            acc += up
        else:
            a -= 1
            acc += down
    to_price = lambda k: (base + k + 0.5) * bin_size  # noqa: E731
    return to_price(poc), to_price(b) + bin_size / 2, to_price(a) - bin_size / 2


def daily_profiles(m1: pd.DataFrame, tday: pd.DatetimeIndex, d1: pd.DataFrame, bin_size: float, va_share: float) -> pd.DataFrame:
    """Jeden riadok na obchodný deň: poc, vah, val, available_at (= uzavretie dňa)."""
    rows = []
    close_by_period = dict(zip(d1["period"], d1["close_time"]))
    low, high = m1["low"].to_numpy(), m1["high"].to_numpy()
    vol = np.maximum(m1["volume"].to_numpy().astype(float), 1.0)
    codes, uniques = pd.factorize(tday, sort=True)
    bounds = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(codes)]])
    for s, e in zip(starts, ends):
        day = uniques[codes[s]]
        if day not in close_by_period:
            continue  # nedokončený deň
        poc, vah, val = _profile(low[s:e], high[s:e], vol[s:e], bin_size, va_share)
        rows.append((close_by_period[day], poc, vah, val))
    return pd.DataFrame(rows, columns=["available_at", "poc", "vah", "val"])


def naked_poc_touch_times(profiles: pd.DataFrame, m5: pd.DataFrame) -> np.ndarray:
    """Čas (int64 ns), kedy cena prvýkrát po skončení dňa prešla cez jeho POC.

    NEVER = POC je stále naked.
    """
    open_t = to_ns(m5.index)
    close_t = to_ns(m5["close_time"])
    high, low = m5["high"].to_numpy(), m5["low"].to_numpy()
    out = np.full(len(profiles), NEVER, dtype=np.int64)
    for k, (avail, poc) in enumerate(zip(to_ns(profiles["available_at"]), profiles["poc"].to_numpy())):
        s = np.searchsorted(open_t, avail)
        hit = (low[s:] <= poc) & (high[s:] >= poc)
        if hit.any():
            out[k] = close_t[s + int(np.argmax(hit))]
    return out
