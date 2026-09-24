"""Načítanie M1 dát: OANDA v20 API (s cache na disku) alebo umelé dáta na testovanie."""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .timeframes import NY

COLUMNS = ["open", "high", "low", "close", "volume",
           "bid_open", "bid_high", "bid_low", "bid_close",
           "ask_open", "ask_high", "ask_low", "ask_close"]

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}


class OandaClient:
    def __init__(self, token: str | None = None, environment: str = "practice"):
        self.token = token or os.environ.get("OANDA_API_TOKEN")
        if not self.token:
            raise RuntimeError("Chýba OANDA API token – nastav premennú prostredia OANDA_API_TOKEN.")
        self.base = HOSTS[os.environ.get("OANDA_ENV", environment)]
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}", "Accept-Datetime-Format": "RFC3339"})

    def candles(self, instrument: str, start: pd.Timestamp, end: pd.Timestamp | None = None, granularity: str = "M1") -> pd.DataFrame:
        """Stiahne sviečky od start do end po dávkach 5000 (iba uzavreté sviečky)."""
        rows = []
        cursor = start
        end = end or pd.Timestamp.now(tz="UTC")
        while cursor < end:
            params = {"price": "MBA", "granularity": granularity, "from": cursor.isoformat(), "count": 5000}
            for attempt in range(5):
                r = self.session.get(f"{self.base}/v3/instruments/{instrument}/candles", params=params, timeout=30)
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            batch = r.json().get("candles", [])
            batch = [c for c in batch if c.get("complete")]
            if not batch:
                break
            for c in batch:
                t = pd.Timestamp(c["time"])
                if t >= end:
                    break
                m, b, a = c["mid"], c["bid"], c["ask"]
                rows.append((t, float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]), int(c["volume"]),
                             float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]),
                             float(a["o"]), float(a["h"]), float(a["l"]), float(a["c"])))
            last = pd.Timestamp(batch[-1]["time"])
            if last <= cursor:
                break
            cursor = last + pd.Timedelta(minutes=1)
            print(f"  stiahnuté po {last:%Y-%m-%d %H:%M} UTC ({len(rows)} sviečok)", flush=True)
        return _to_frame(rows)


def _to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time"] + COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time").sort_index()


def load_oanda(cfg: dict, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """M1 z OANDA s lokálnou cache (data/<instrument>_M1.parquet), dopĺňa len chýbajúce dáta."""
    inst = cfg["instrument"]
    path = Path(cfg["data"]["cache_dir"]) / f"{inst}_M1.parquet"
    start_ts = pd.Timestamp(start or cfg["data"]["start"], tz="UTC")
    end_ts = pd.Timestamp(end or cfg["data"]["end"], tz="UTC") if (end or cfg["data"]["end"]) else None
    client = OandaClient(environment=cfg["oanda"]["environment"])
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
    parts = [cached]
    if len(cached) and cached.index[0] > start_ts + pd.Timedelta(days=3):
        print(f"Sťahujem {inst} M1 {start_ts:%Y-%m-%d} – {cached.index[0]:%Y-%m-%d}")
        parts.append(client.candles(inst, start_ts, cached.index[0]))
    fetch_from = cached.index[-1] + pd.Timedelta(minutes=1) if len(cached) else start_ts
    print(f"Sťahujem {inst} M1 od {fetch_from:%Y-%m-%d %H:%M} UTC")
    parts.append(client.candles(inst, fetch_from, end_ts))
    df = pd.concat([p for p in parts if len(p)])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    df = df[df.index >= start_ts]
    return df[df.index < end_ts] if end_ts is not None else df


def load_recent(cfg: dict, days: int) -> pd.DataFrame:
    """Posledných N dní M1 priamo z OANDA (pre živé skenovanie)."""
    client = OandaClient(environment=cfg["oanda"]["environment"])
    return client.candles(cfg["instrument"], pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days))


def make_sample(start: str = "2024-01-01", days: int = 120, seed: int = 7, price: float = 1.09) -> pd.DataFrame:
    """Umelé M1 dáta podobné EUR/USD (random walk s volatilitou podľa seansy).

    Iba na otestovanie, že všetko beží – výsledky backtestu na nich nič nehovoria.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=days * 1440, freq="1min")
    ny = idx.tz_convert(NY)
    wd, hr = ny.weekday, ny.hour
    open_mkt = ~(((wd == 4) & (hr >= 17)) | (wd == 5) | ((wd == 6) & (hr < 17)))
    idx, ny = idx[open_mkt], ny[open_mkt]
    h = ny.hour.to_numpy()
    vol = np.where((h >= 2) & (h < 11), 0.9, np.where((h >= 11) & (h < 16), 0.6, 0.3)) * 1e-4
    trend = np.cumsum(rng.normal(0, 1, len(idx))) * 1e-7
    steps = rng.standard_t(4, len(idx)) * vol / np.sqrt(2) + np.diff(np.concatenate([[0], trend]))
    close = price + np.cumsum(steps)
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.normal(0, 1, (2, len(idx)))) * vol * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    spread = np.where((h >= 17) & (h < 19), 1.6, 0.8) * 1e-4
    ticks = np.maximum(1, (vol * 1e4 * 60 * rng.uniform(0.5, 1.5, len(idx)))).astype(int)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": ticks}, index=idx)
    for side, sgn in (("bid", -0.5), ("ask", 0.5)):
        for k in ("open", "high", "low", "close"):
            df[f"{side}_{k}"] = df[k] + sgn * spread
    df.index.name = "time"
    return df[COLUMNS].round(6)


def load_m1(cfg: dict, source: str | None = None, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    source = source or cfg["data"]["source"]
    if source == "sample":
        return make_sample(start or cfg["data"]["start"])
    if source == "oanda":
        return load_oanda(cfg, start, end)
    path = Path(source)  # vlastný parquet/csv súbor
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df
