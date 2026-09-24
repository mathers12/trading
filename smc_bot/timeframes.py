"""Resampling M1 dát na vyššie timeframy.

Forexový obchodný deň začína o 17:00 New York (tak ako denné sviečky OANDA).
Čas preto posúvame o +7 h: 17:00 NY -> 00:00 "posunutého" času. Potom
D1/H4/W1 sviečky sedia presne s OANDA a zmena letného času sa rieši sama.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NY = "America/New_York"
SHIFT = pd.Timedelta(hours=7)

FREQ = {
    "M1": pd.Timedelta(minutes=1),
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1),
    "W1": pd.Timedelta(days=7),
}


NEVER = np.iinfo(np.int64).max


def to_ns(x) -> np.ndarray:
    """Časy (index / séria, s časovou zónou) -> int64 nanosekundy UTC."""
    return pd.DatetimeIndex(x).as_unit("ns").asi8


def shifted_naive(index_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC -> naivný NY čas posunutý o +7 h (obchodný deň začína o 00:00)."""
    return index_utc.tz_convert(NY).tz_localize(None) + SHIFT


def trading_day(index_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return shifted_naive(index_utc).normalize()


def ny_minutes(index_utc: pd.DatetimeIndex) -> np.ndarray:
    ny = index_utc.tz_convert(NY)
    return (ny.hour * 60 + ny.minute).to_numpy()


def period_key(index_utc: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    s = shifted_naive(index_utc)
    if tf == "W1":
        d = s.normalize()
        return d - pd.to_timedelta(d.weekday, unit="D")
    return s.floor(FREQ[tf])


def resample(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Z M1 (mid ceny) spraví sviečky daného timeframu.

    Index = čas otvorenia (UTC). Stĺpec close_time = kedy je sviečka uzavretá,
    teda odkedy ju smie stratégia použiť. Nedokončená posledná sviečka sa zahodí.
    """
    if tf == "M1":
        out = m1[["open", "high", "low", "close", "volume"]].copy()
        out["close_time"] = out.index + FREQ["M1"]
        return out

    key = period_key(m1.index, tf)
    offset = m1.index.tz_localize(None) - shifted_naive(m1.index)  # UTC - posunutý čas
    g = pd.DataFrame(
        {
            "key": key,
            "offset": offset,
            "open": m1["open"].to_numpy(),
            "high": m1["high"].to_numpy(),
            "low": m1["low"].to_numpy(),
            "close": m1["close"].to_numpy(),
            "volume": m1["volume"].to_numpy(),
        }
    ).groupby("key", sort=True)
    out = g.agg(
        offset=("offset", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    keys = out.index.to_series()
    open_utc = pd.DatetimeIndex(keys + out["offset"]).tz_localize("UTC")
    close_utc = pd.DatetimeIndex(keys + FREQ[tf] + out["offset"]).tz_localize("UTC")
    res = pd.DataFrame(
        {
            "open": out["open"].to_numpy(),
            "high": out["high"].to_numpy(),
            "low": out["low"].to_numpy(),
            "close": out["close"].to_numpy(),
            "volume": out["volume"].to_numpy(),
            "close_time": close_utc,
            "period": keys.to_numpy(),
        },
        index=open_utc,
    )
    res.index.name = "time"
    last_available = m1.index[-1] + FREQ["M1"]
    return res[res["close_time"] <= last_available]
