import lzma

import numpy as np
import pandas as pd

from smc_bot import dukascopy


def _bi5(rows):
    """rows: (sekundy, open, close, low, high, volume) v bodoch (× 0.00001)."""
    arr = np.array(rows, dtype=dukascopy.RECORD)
    return lzma.compress(arr.tobytes(), format=lzma.FORMAT_ALONE)


def test_decode_prices_and_time():
    day = pd.Timestamp("2024-02-05")
    raw = _bi5([(0, 108000, 108010, 107990, 108020, 1.5), (60, 108010, 107995, 107980, 108015, 2.0)])
    df = dukascopy.decode(raw, day, 0.00001)
    assert list(df.index) == [pd.Timestamp("2024-02-05 00:00"), pd.Timestamp("2024-02-05 00:01")]
    first = df.iloc[0]
    assert np.isclose(first["open"], 1.08) and np.isclose(first["close"], 1.0801)
    assert np.isclose(first["low"], 1.0799) and np.isclose(first["high"], 1.0802)


def test_fetch_day_builds_mid_bid_ask_and_drops_flat(monkeypatch):
    bid = _bi5([(0, 108000, 108010, 107990, 108020, 1.0), (60, 108010, 108010, 108010, 108010, 0.0)])
    ask = _bi5([(0, 108008, 108018, 107998, 108028, 1.0), (60, 108018, 108018, 108018, 108018, 0.0)])
    monkeypatch.setattr(dukascopy, "_get", lambda s, url: bid if "BID" in url else ask)
    df = dukascopy.fetch_day(None, "EUR_USD", pd.Timestamp("2024-02-05"))
    assert len(df) == 1  # sviečka s nulovým objemom (víkend) je vyhodená
    row = df.iloc[0]
    assert np.isclose(row["ask_close"] - row["bid_close"], 0.00008)
    assert np.isclose(row["close"], (row["ask_close"] + row["bid_close"]) / 2)
    assert str(df.index.tz) == "UTC"


def test_url_month_is_zero_based(monkeypatch):
    seen = []
    monkeypatch.setattr(dukascopy, "_get", lambda s, url: seen.append(url) or b"")
    dukascopy.fetch_day(None, "EUR_USD", pd.Timestamp("2024-01-05"))
    assert seen[0] == "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/00/05/BID_candles_min_1.bi5"
