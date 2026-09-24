"""Načítanie konfigurácie (config.yaml) s predvolenými hodnotami."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "instrument": "EUR_USD",
    "pip": 0.0001,
    "data": {"source": "oanda", "start": "2024-01-01", "end": None, "cache_dir": "data"},
    "oanda": {"environment": "practice"},
    "sessions": {
        "asia": "20:00-00:00",
        "london": "02:00-05:00",
        "ny_am": "07:00-10:00",
        "ny_pm": "13:30-16:00",
    },
    "bias": {"filter": "d1", "fallback": ["w1", "liquidity"]},
    "liquidity": {
        "external": ["pwh_pwl", "pdh_pdl", "asia", "h1_swings", "m15_swings"],
        "internal_fvg_timeframes": ["H1"],
        "swing_strength": 2,
        "max_age_days": 5,
    },
    "structure": {
        "swing_strength_m5": 2,
        "atr_period": 14,
        "fvg_min_atr": 0.1,
        "displacement_body_atr": 1.5,
        "require_displacement": True,
        "mss_max_bars": 36,
        "mss_ref_lookback": 48,
    },
    "poi": {"timeframes": ["H4", "H1", "M15"], "tolerance_pips": 2.0, "max_age_days": 10},
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
        "kinds": ["pdh_pdl", "pwh_pwl", "asia", "h1_swings", "m15_swings"],
    },
    "filters": {
        "signal_windows_ny": ["00:00-14:00"],
        "require_htf_poi": False,
        "require_h4_crt": False,
        "require_inducement": False,
        "require_vp": False,
        "require_premium_discount": False,
        "require_killzone": False,
    },
    "risk": {
        "risk_per_trade_pct": 1.0,
        "max_trades_per_day": 2,
        "max_losses_per_day": 2,
        "one_position_at_a_time": True,
        "eod_exit_ny": "16:00",
    },
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
