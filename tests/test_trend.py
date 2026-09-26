import numpy as np
import pandas as pd

from smc_bot import trend


def _m1(days=6, seed=0):
    idx = pd.date_range("2025-01-06", periods=days * 1440, freq="1min")
    c = 20000 + np.cumsum(np.random.default_rng(seed).normal(0, 3, len(idx)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"o": o, "h": np.maximum(o, c) + 1, "l": np.minimum(o, c) - 1, "c": c}, index=idx)


def test_features_no_lookahead():
    m1 = _m1()
    cut = pd.Timestamp("2025-01-09 13:37")
    full, part = trend.features(m1), trend.features(m1[m1.index < cut])
    known = part.index[part.index <= cut.floor("5min")]
    cols = ["c", "ema200", "ch4h", "hh4h", "dayloc", "atr"]
    pd.testing.assert_frame_equal(full.loc[known, cols], part.loc[known, cols])


def test_backtest_runs_and_is_consistent():
    m1 = _m1()
    t = trend.backtest(m1, trend.Params(dayloc=0.0), spread=1.0)
    assert len(t) > 0
    assert (t.exit_time >= t.time).all()
    assert (t.R >= -1.5).all()      # strata najviac ~1R (+ gap), SL sa kontroluje prvý
    assert set(t.side) <= {1, -1}
