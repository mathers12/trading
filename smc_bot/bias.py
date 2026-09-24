"""Daily / weekly bias podľa dvoch posledných uzavretých sviečok.

C1 = predposledná, C2 = posledná uzavretá sviečka.
  BULLISH: C2 zatvorí nad high C1, alebo C2 vyberie low C1 a zatvorí späť v rozsahu
  BEARISH: C2 zatvorí pod low C1, alebo C2 vyberie high C1 a zatvorí späť v rozsahu
  AVOID (0): inside bar, alebo C2 vyberie obe strany a zatvorí vnútri
"""
from __future__ import annotations

import numpy as np

BULLISH, BEARISH, NEUTRAL = 1, -1, 0


def candle_bias(h1: float, l1: float, h2: float, l2: float, c2: float) -> int:
    if c2 > h1:
        return BULLISH
    if c2 < l1:
        return BEARISH
    took_high, took_low = h2 > h1, l2 < l1
    if took_high and not took_low:
        return BEARISH
    if took_low and not took_high:
        return BULLISH
    return NEUTRAL


def bias_series(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """bias[k] = bias po uzavretí sviečky k (platí pre nasledujúcu periódu)."""
    out = np.zeros(len(high), dtype=int)
    for k in range(1, len(high)):
        out[k] = candle_bias(high[k - 1], low[k - 1], high[k], low[k], close[k])
    return out


def label(b: int) -> str:
    return {BULLISH: "bullish", BEARISH: "bearish"}.get(b, "neutral")
