"""Stavebné kamene price action: ATR, swingy, FVG, order blocky."""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def swing_highs(high: np.ndarray, n: int) -> np.ndarray:
    """Swing high na i: high[i] je vyšší ako n sviečok vľavo a >= n sviečok vpravo.

    Swing je potvrdený až po uzavretí sviečky i + n.
    """
    s = pd.Series(high)
    left = s.shift(1).rolling(n).max()
    right = s[::-1].shift(1).rolling(n).max()[::-1]
    return ((s > left) & (s >= right)).to_numpy()


def swing_lows(low: np.ndarray, n: int) -> np.ndarray:
    return swing_highs(-low, n)


def find_fvgs(high: np.ndarray, low: np.ndarray, min_size: np.ndarray | float) -> pd.DataFrame:
    """Fair value gapy. i = index tretej sviečky (FVG je známy po jej uzavretí).

    bull: low[i] > high[i-2], zóna [high[i-2], low[i]]
    bear: high[i] < low[i-2], zóna [high[i], low[i-2]]
    """
    n = len(high)
    if n < 3:
        return pd.DataFrame(columns=["idx", "side", "bottom", "top"])
    min_size = np.broadcast_to(np.asarray(min_size, dtype=float), (n,))
    h2, l2 = high[:-2], low[:-2]
    h0, l0 = high[2:], low[2:]
    ms = min_size[2:]
    bull = (l0 - h2) > np.maximum(ms, 0)
    bear = (l2 - h0) > np.maximum(ms, 0)
    idx = np.arange(2, n)
    parts = [
        pd.DataFrame({"idx": idx[bull], "side": "bull", "bottom": h2[bull], "top": l0[bull]}),
        pd.DataFrame({"idx": idx[bear], "side": "bear", "bottom": h0[bear], "top": l2[bear]}),
    ]
    return pd.concat(parts, ignore_index=True).sort_values("idx", kind="stable").reset_index(drop=True)


def find_order_blocks(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, fvgs: pd.DataFrame, lookback: int = 5
) -> pd.DataFrame:
    """Order block = posledná sviečka opačnej farby pred displacementom, ktorý nechal FVG.

    Známy v rovnakom čase ako FVG (po uzavretí tretej sviečky).
    """
    rows = []
    for idx, side in zip(fvgs["idx"].to_numpy(), fvgs["side"].to_numpy()):
        start = idx - 2
        for k in range(start, max(start - lookback, -1), -1):
            opposite = close[k] < open_[k] if side == "bull" else close[k] > open_[k]
            if opposite:
                rows.append((idx, side, low[k], high[k]))
                break
    return pd.DataFrame(rows, columns=["idx", "side", "bottom", "top"])
