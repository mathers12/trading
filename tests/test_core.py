import numpy as np
import pandas as pd
import pytest

from smc_bot import engine
from smc_bot.backtest import simulate
from smc_bot.bias import BEARISH, BULLISH, NEUTRAL, candle_bias
from smc_bot.config import load_config
from smc_bot.context import build_context, in_window, parse_window
from smc_bot.data import make_sample
from smc_bot.structure import find_fvgs, swing_highs
from smc_bot.timeframes import resample


# --- daily bias podľa obrázka (C1 = high 1.10 / low 1.09)
@pytest.mark.parametrize(
    "h2,l2,c2,expected",
    [
        (1.1010, 1.0950, 1.0960, BEARISH),  # sweep high, close späť v rozsahu
        (1.1030, 1.0950, 1.1020, BULLISH),  # close nad high
        (1.0990, 1.0910, 1.0950, NEUTRAL),  # inside bar
        (1.1010, 1.0890, 1.0950, NEUTRAL),  # vybral obe strany, close vnútri
        (1.0960, 1.0870, 1.0880, BEARISH),  # close pod low
        (1.0980, 1.0890, 1.0970, BULLISH),  # sweep low, close späť v rozsahu
    ],
)
def test_candle_bias(h2, l2, c2, expected):
    assert candle_bias(1.1000, 1.0900, h2, l2, c2) == expected


def test_windows():
    asia = parse_window("20:00-00:00")
    assert in_window(20 * 60, asia) and in_window(23 * 60 + 59, asia) and not in_window(0, asia)
    assert in_window(3 * 60, parse_window("02:00-05:00"))


def test_fvg_and_swings():
    high = np.array([1.0, 1.1, 1.5, 1.6, 1.4])
    low = np.array([0.9, 1.0, 1.2, 1.3, 1.2])
    fv = find_fvgs(high, low, 0.0)
    bull = fv[fv.side == "bull"].iloc[0]
    assert bull.idx == 2 and bull.bottom == 1.0 and bull.top == 1.2
    sh = swing_highs(np.array([1, 2, 5, 3, 2, 1.0]), 2)
    assert sh.tolist() == [False, False, True, False, False, False]


def test_daily_candles_start_17_ny():
    m1 = make_sample("2024-03-04", days=3)
    d1 = resample(m1, "D1")
    # denná sviečka začína 17:00 NY = 22:00 UTC (zimný čas)
    assert (d1.index.tz_convert("America/New_York").hour == 17).all()
    assert (d1["close_time"] - d1.index == pd.Timedelta(days=1)).all()


def _m1(rows, start="2024-01-02 10:00"):
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=len(rows), freq="1min")
    lo, hi = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    a = {"open_ns": idx.as_unit("ns").asi8}
    for side, sh in (("bid", 0.0), ("ask", 0.0001)):
        a[f"{side}_open"], a[f"{side}_low"], a[f"{side}_high"], a[f"{side}_close"] = (lo + hi) / 2 + sh, lo + sh, hi + sh, (lo + hi) / 2 + sh
    return idx, a


def _setup(idx, **kw):
    s = {"direction": "LONG", "entry": 1.1000, "sl": 1.0990, "tp": 1.1020, "signal_ns": idx[0].value,
         "expiry_ns": idx[-1].value + 1, "eod_ns": idx[-1].value + 10**12}
    s.update(kw)
    return s


def test_simulate_tp_and_sl_and_same_bar():
    idx, a = _m1([(1.1005, 1.1010), (1.0998, 1.1004), (1.1003, 1.1015), (1.1010, 1.1025)])
    r = simulate(_setup(idx), a)
    assert r["status"] == "FILLED" and r["result"] == "TP" and r["r"] == pytest.approx(2.0)

    idx, a = _m1([(1.0998, 1.1004), (1.0985, 1.1025)])  # SL aj TP v jednej sviečke -> SL
    r = simulate(_setup(idx), a)
    assert r["result"] == "SL" and r["r"] == pytest.approx(-1.0)

    idx, a = _m1([(1.1005, 1.1010), (1.1010, 1.1030)])  # TP pred naplnením -> MISSED
    assert simulate(_setup(idx), a)["status"] == "MISSED"


def test_short_uses_ask_for_exit():
    # short: SL sa trafí cez ask (bid + 1 pip spread)
    idx, a = _m1([(1.0995, 1.1001), (1.1000, 1.10095)])
    r = simulate(_setup(idx, direction="SHORT", entry=1.1000, sl=1.1010, tp=1.0980), a)
    assert r["result"] == "SL"


def test_no_lookahead():
    """Setupy nájdené na skrátených dátach musia byť rovnaké ako na celých dátach."""
    cfg = load_config(None, ["bias.filter=false", "filters.signal_windows_ny=[]"])
    m1 = make_sample("2024-01-01", days=45, seed=3)
    cut = m1.index[len(m1) * 2 // 3]
    full = engine.run(build_context(m1, cfg), simulate_trades=False)["setups"]
    part = engine.run(build_context(m1[m1.index < cut], cfg), simulate_trades=False)["setups"]
    key = lambda s: (s["signal_ns"], s["direction"], s["entry"], s["sl"], s["tp"], s["h4_crt"], s["htf_poi"], s["bias"])  # noqa: E731
    horizon = cut.value - 6 * 3600 * 10**9  # ponecháme rezervu na potvrdenie swingov
    a = [key(s) for s in full if s["signal_ns"] < horizon]
    b = [key(s) for s in part if s["signal_ns"] < horizon]
    assert len(a) > 5
    assert a == b


def test_backtest_runs_on_sample():
    cfg = load_config("config.yaml")
    res = engine.run(build_context(make_sample(days=40), cfg))
    assert res["setups"], "na umelých dátach by sa mal nájsť aspoň jeden setup"
    for s in res["setups"]:
        long = s["direction"] == "LONG"
        assert (s["sl"] < s["entry"] < s["tp"]) if long else (s["tp"] < s["entry"] < s["sl"])
        assert s["rr"] >= cfg["target"]["min_rr"]


def test_local_trading_window_and_exit():
    cfg = load_config("config.yaml", ["bias.filter=false"])
    res = engine.run(build_context(make_sample(days=40), cfg))
    assert res["setups"]
    for s in res["setups"]:
        lt = s["signal_time"].tz_convert("Europe/Bratislava")
        assert 8 * 60 <= lt.hour * 60 + lt.minute < 19 * 60
        eod = pd.Timestamp(s["eod_ns"], tz="UTC").tz_convert("Europe/Bratislava")
        assert (eod.hour, eod.minute) == (19, 0)
        if s.get("exit_ns"):
            assert s["exit_ns"] <= s["eod_ns"] + 60 * 10**9  # najneskôr o 19:00 (+ posledná M1 sviečka)


def test_smt_tag_with_second_pair():
    cfg = load_config("config.yaml", ["bias.filter=false"])
    m1 = make_sample(days=40)
    other = make_sample(days=40, seed=11)
    setups = engine.run(build_context(m1, cfg, other))["setups"]
    assert setups and all(isinstance(s["smt"], bool) for s in setups)
    assert all(s["smt"] is False for s in engine.run(build_context(m1, cfg))["setups"])
