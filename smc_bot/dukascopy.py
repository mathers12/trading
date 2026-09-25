"""Dukascopy – bezplatná história M1 bid/ask (bez registrácie) a Twelve Data na dnešné sviečky.

Dukascopy zverejňuje denné súbory M1 sviečok:
  https://datafeed.dukascopy.com/datafeed/EURUSD/2024/00/05/BID_candles_min_1.bi5
(mesiac je číslovaný od 00). Súbor je LZMA a obsahuje 24-bajtové záznamy
  čas (s od polnoci UTC), open, close, low, high (celé čísla × point), volume (float)
Aktuálny deň ešte nie je zverejnený, preto živé skenovanie dopĺňa posledné
hodiny z Twelve Data (bezplatný API kľúč).
"""
from __future__ import annotations

import lzma
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
RECORD = np.dtype([("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")])


def symbol(instrument: str) -> str:
    return instrument.replace("_", "").replace("/", "").upper()


def point(instrument: str) -> float:
    return 0.001 if "JPY" in instrument.upper() else 0.00001


def decode(raw: bytes, day: pd.Timestamp, pt: float) -> pd.DataFrame:
    """Dekóduje .bi5 súbor s M1 sviečkami jedného dňa."""
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    rec = np.frombuffer(lzma.decompress(raw), dtype=RECORD)
    o, c, lo, hi = (rec[k].astype(float) * pt for k in ("o", "c", "l", "h"))
    # poistka: keby bolo poradie stĺpcov iné, low/high dopočítame zo všetkých štyroch
    low = np.minimum.reduce([o, c, lo, hi])
    high = np.maximum.reduce([o, c, lo, hi])
    idx = pd.DatetimeIndex(day + pd.to_timedelta(rec["t"].astype(np.int64), unit="s"))
    return pd.DataFrame({"open": o, "high": high, "low": low, "close": c, "volume": rec["v"].astype(float)}, index=idx)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.dukascopy.com/",
}


class DownloadError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(session: requests.Session, url: str, attempts: int = 5, timeout: float = 30) -> bytes:
    last = ""
    for attempt in range(attempts):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 404:
                return b""
            if r.status_code == 200:
                return r.content
            last = f"HTTP {r.status_code}: {r.text[:120]!r}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0 * 2 ** attempt)
    raise DownloadError(f"Dukascopy: nepodarilo sa stiahnuť {url} ({last})")


def fetch_day(session: requests.Session, instrument: str, day: pd.Timestamp, fast: bool = False) -> pd.DataFrame:
    sym, pt = symbol(instrument), point(instrument)
    opts = {"attempts": 2, "timeout": 10} if fast else {}
    sides = {}
    for side in ("BID", "ASK"):
        url = URL.format(sym=sym, y=day.year, m=day.month - 1, d=day.day, side=side)
        sides[side] = decode(_get(session, url, **opts), day, pt)
    bid, ask = sides["BID"], sides["ASK"]
    if not len(bid) or not len(ask):
        return pd.DataFrame()
    df = bid.join(ask, how="inner", lsuffix="_b", rsuffix="_a")
    # víkend a sviatky: Dukascopy dáva ploché sviečky s nulovým objemom
    df = df[(df["volume_b"] > 0) | (df["volume_a"] > 0)]
    out = pd.DataFrame(index=df.index)
    for k in ("open", "high", "low", "close"):
        out[f"bid_{k}"] = df[f"{k}_b"]
        out[f"ask_{k}"] = df[f"{k}_a"]
        out[k] = (df[f"{k}_b"] + df[f"{k}_a"]) / 2
    out["volume"] = (df["volume_b"] + df["volume_a"]) / 2
    out.index = out.index.tz_localize("UTC") if out.index.tz is None else out.index
    return out


def trading_days(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Dni [start, end) bez sobôt (v sobotu je forex zavretý)."""
    return [d for d in pd.date_range(start.normalize(), end.normalize(), freq="D", inclusive="left") if d.weekday() != 5]


def fetch_days(instrument: str, days: list[pd.Timestamp], workers: int = 4, fast: bool = False) -> pd.DataFrame:
    """Stiahne zadané dni paralelne.

    Dni, ktoré sa nepodarí stiahnuť, sa na konci skúsia ešte raz po jednom.
    fast=True (živé skenovanie): krátke timeouty a chýbajúce dni iba vypíše,
    doplnia sa pri ďalšom behu. Inak skončí chybou, ak zlyhá viac ako 2 % dní.
    """
    if not days:
        return pd.DataFrame()
    local = threading.local()

    def one(d):
        if not hasattr(local, "session"):
            local.session = _session()
        try:
            return d, fetch_day(local.session, instrument, d.tz_localize(None), fast), ""
        except DownloadError as e:
            return d, None, str(e)

    frames: list[pd.DataFrame] = []
    failed: list[tuple[pd.Timestamp, str]] = []
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for d, df, err in pool.map(one, days):
            if df is None:
                failed.append((d, err))
                print(f"  ! {d:%Y-%m-%d}: {err}", flush=True)
                if len(failed) >= 8 and not frames and not fast:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise DownloadError(f"Dukascopy nevracia dáta (prvých {len(failed)} dní zlyhalo). Posledná chyba: {err}")
            else:
                frames.append(df)
            done += 1
            if done % 50 == 0 or done == len(days):
                print(f"  Dukascopy: {done}/{len(days)} dní", flush=True)
    if failed and not fast:
        print(f"  Opakujem {len(failed)} nestiahnutých dní…", flush=True)
        session, still = _session(), []
        for d, _ in failed:
            time.sleep(2)
            try:
                frames.append(fetch_day(session, instrument, d.tz_localize(None)))
            except DownloadError as e:
                still.append((d, str(e)))
        failed = still
    if failed:
        msg = f"Nestiahnuté dni ({len(failed)}/{len(days)}): " + ", ".join(f"{d:%Y-%m-%d}" for d, _ in failed[:10])
        if not fast and len(failed) > 0.02 * len(days):
            raise DownloadError(msg + f"\nPosledná chyba: {failed[-1][1]}")
        print("  VAROVANIE: " + msg, flush=True)
    frames = [f for f in frames if len(f)]
    return pd.concat(frames).sort_index() if frames else pd.DataFrame()


def load(cfg: dict, start: str | None = None, end: str | None = None, fast: bool = False) -> pd.DataFrame:
    """M1 z Dukascopy s cache (data/<instrument>_M1_dukascopy.parquet).

    Sťahuje iba dni, ktoré v cache chýbajú (aj diery po predošlých výpadkoch).
    Dnešný deň Dukascopy ešte nemá, preto končí včerajškom.
    """
    from .data import COLUMNS

    inst = cfg["instrument"]
    path = Path(cfg["data"]["cache_dir"]) / f"{inst}_M1_dukascopy.parquet"
    start_ts = pd.Timestamp(start or cfg["data"]["start"], tz="UTC")
    today = pd.Timestamp.now(tz="UTC").normalize()
    end_arg = end or cfg["data"].get("end")
    end_ts = min(pd.Timestamp(end_arg, tz="UTC"), today) if end_arg else today
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
    have = set(cached.index.normalize()) if len(cached) else set()
    missing = [d for d in trading_days(start_ts, end_ts) if d not in have]
    new = pd.DataFrame()
    if missing:
        print(f"Sťahujem {inst} M1 z Dukascopy: {len(missing)} dní ({missing[0]:%Y-%m-%d} – {missing[-1]:%Y-%m-%d})")
        new = fetch_days(inst, missing, fast=fast)
    parts = [p for p in (cached, new) if len(p)]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="last")].sort_index()[COLUMNS]
    if len(new):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    return df[(df.index >= start_ts) & (df.index < end_ts)]


def twelvedata_recent(instrument: str, spread_pips: float, pip: float, bars: int = 5000) -> pd.DataFrame:
    """Posledné M1 sviečky z Twelve Data (mid ceny). Bid/ask sa odvodí z predpokladaného spreadu."""
    from .data import COLUMNS

    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        print("TWELVEDATA_API_KEY nie je nastavený – dnešné sviečky chýbajú, skenujem iba po včerajšok.")
        return pd.DataFrame(columns=COLUMNS)
    sym = instrument.replace("_", "/")
    r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
        "symbol": sym, "interval": "1min", "outputsize": bars, "timezone": "UTC", "order": "asc", "apikey": key})
    r.raise_for_status()
    js = r.json()
    if js.get("status") != "ok":
        raise RuntimeError(f"Twelve Data chyba: {js.get('message', js)}")
    v = pd.DataFrame(js["values"])
    idx = pd.to_datetime(v["datetime"]).dt.tz_localize("UTC")
    df = pd.DataFrame({k: v[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")}, index=pd.DatetimeIndex(idx))
    df = df[df.index + pd.Timedelta(minutes=1) <= pd.Timestamp.now(tz="UTC")]  # iba uzavreté sviečky
    df["volume"] = 1.0
    half = spread_pips * pip / 2
    for k in ("open", "high", "low", "close"):
        df[f"bid_{k}"] = df[k] - half
        df[f"ask_{k}"] = df[k] + half
    df.index.name = "time"
    return df[COLUMNS]
