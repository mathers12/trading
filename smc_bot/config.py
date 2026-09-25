"""Načítanie konfigurácie (config.yaml) s predvolenými hodnotami."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "strategy": "smc",  # smc | asia_breakout | asia_fakeout | value_revert (smc_bot/strategies.py)
    # HTF pullback model (liquidity.external obsahuje htf_pb; smc_bot/htf.py)
    "htf": {"timeframe": "H1", "poi_timeframe": "H1", "swing_strength": 2, "fib_min": 0.62, "fib_max": 0.79,
            "require_fvg": True, "max_touches": 1,
            "align": [], "min_impulse_atr": 0, "idm_max_pips": 1e9},  # top-down: TF, ktorých štruktúra (smer posledného BOS) musí súhlasiť, napr. [W1, D1, H4]  # max_touches > 1: nový vstup pri každom návrate do zóny
    # dôležité správy: 2 h pred a 30 min po správe žiadne nové signály (calendar/news.csv, čas NY)
    "news": {"enabled": False, "file": "calendar/news.csv", "before_min": 120, "after_min": 30},
    "session_strategy": {
        "windows_local": ["08:00-11:00"], "direction": "none", "entry": "market", "rr": 2.0, "min_rr": 1.0,
        "min_range_pips": 5, "max_range_pips": 40, "break_pips": 0.0, "sweep_pips": 1.0, "sl": "mid", "sl_atr": 1.5,
        "sl_buffer_pips": 2.0, "min_sl_pips": 3, "max_sl_pips": 30, "tp": "opposite", "confirm_bars": 6,
    },
    "instrument": "EUR_USD",
    "instruments": ["EUR_USD", "GBP_USD"],
    "pip": 0.0001,
    "local_timezone": "Europe/Bratislava",
    "data": {"source": "dukascopy", "start": "2024-01-01", "end": None, "cache_dir": "data", "assumed_spread_pips": 0.8},
    "oanda": {"environment": "practice"},
    "sessions": {
        "asia": "20:00-00:00",
        "london": "02:00-05:00",
        "ny_am": "07:00-10:00",
        "ny_pm": "13:30-16:00",
    },
    "bias": {"filter": True, "fallback": ["w1", "liquidity"]},
    "liquidity": {
        "external": ["pwh_pwl", "pdh_pdl", "asia", "london", "h1_swings", "m15_swings"],
        "internal_fvg_timeframes": ["H1"],
        "swing_strength": 2,
        "max_age_days": 5,
    },
    "structure": {
        "timeframe": "M5",
        "swing_strength_m5": 2,
        "atr_period": 14,
        "fvg_min_atr": 0.1,
        "displacement_body_atr": 1.5,
        "require_displacement": True,
        "mss_max_bars": 36,
        "mss_ref_lookback": 48,
    },
    "poi": {"timeframes": ["H4", "H1", "M15"], "tolerance_pips": 2.0, "max_age_days": 10,
            # POI stratégia (liquidity.external obsahuje poi_zone): OB zóny, iba prvý dotyk
            "zone_timeframes": ["H4", "H1", "M15"], "zone_max_age_days": 10, "require_bos": True,
            "sl": "extreme", "cancel_on_close_beyond": True},
    "volume_profile": {"bin_pips": 0.5, "value_area": 0.70, "tolerance_pips": 3.0, "npoc_lookback_days": 10},
    "inducement": {"lookback_bars": 24, "max_distance_pips": 10.0},
    "entry": {
        "type": "fvg_or_ob",
        "price": "proximal",
        "expiry_bars": 12,
        "sl_buffer_pips": 1.0,
        "min_sl_pips": 3.0,
        "max_sl_pips": 25.0,
    },
    "target": {
        "min_rr": 2.0,
        "mode": "first_min_rr",
        "max_rr": None,
        "kinds": ["pdh_pdl", "pwh_pwl", "asia", "london", "h1_swings", "m15_swings"],
    },
    "filters": {
        "signal_windows_ny": [],
        "signal_windows_local": ["08:00-19:00"],
        "sweep_kinds": None,
        "require_htf_poi": False,
        "require_h4_crt": False,
        "require_inducement": False,
        "require_vp": False,
        "require_premium_discount": False,
        "require_killzone": False,
        "require_midnight_open": False,
        "require_smt": False,
    },
    "risk": {
        "risk_per_trade_pct": 1.0,
        "max_trades_per_day": 2,
        "max_losses_per_day": 2,
        "one_position_at_a_time": True,
        "eod_exit_ny": "16:00",
        "eod_exit_local": "19:00",
    },
    "smt": {"instrument": "GBP_USD", "lookback_bars": 24},
    "live": {"lookback_days": 25, "alert_max_age_minutes": 60},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_overrides(cfg: dict, overrides: list[str] | None) -> dict:
    """--set sekcia.kluc=hodnota (hodnota sa parsuje ako YAML)."""
    cfg = copy.deepcopy(cfg)
    for item in overrides or []:
        path, _, raw = item.partition("=")
        node = cfg
        keys = path.strip().split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = yaml.safe_load(raw)
    return cfg


def load_config(path: str | Path | None = "config.yaml", overrides: list[str] | None = None) -> dict:
    user: dict = {}
    if path and Path(path).exists():
        user = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return apply_overrides(deep_merge(DEFAULTS, user), overrides)


def pip_size(instrument: str) -> float:
    return 0.01 if "JPY" in instrument.upper() else 0.0001


def for_instrument(cfg: dict, instrument: str) -> dict:
    """Kópia nastavení pre konkrétny trh (pip sa odvodí z názvu)."""
    out = copy.deepcopy(cfg)
    out["instrument"] = instrument
    out["pip"] = pip_size(instrument)
    return out
