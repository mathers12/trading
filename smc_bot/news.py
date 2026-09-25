"""Filter na dôležité správy: pred správou (napr. 2 h) a chvíľu po nej sa neobchoduje."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


@lru_cache(maxsize=4)
def times_ns(path: str) -> np.ndarray:
    p = Path(path)
    if not p.exists():
        return np.array([], dtype=np.int64)
    df = pd.read_csv(p, comment="#")
    t = pd.to_datetime(df["time_ny"]).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    return np.sort(np.array([x.value for x in t], dtype=np.int64))  # ns (nezávisle od jednotky v pandas)


def blocked(cfg: dict, t_ns: int) -> str:
    """Názov dôvodu, ak čas t padá do okna [správa - before, správa + after], inak ''."""
    nc = cfg.get("news") or {}
    if not nc.get("enabled"):
        return ""
    ts = times_ns(nc["file"])
    if not len(ts):
        return ""
    before, after = int(nc["before_min"] * 60e9), int(nc["after_min"] * 60e9)
    k = np.searchsorted(ts, t_ns - after, "left")  # prvá správa, ktorá ešte „neodznela“
    return "správy" if k < len(ts) and ts[k] - before <= t_ns else ""


def next_start(cfg: dict, t_ns: int) -> int | None:
    """Začiatok najbližšieho zakázaného okna po čase t (na zrušenie nenaplnenej limitky)."""
    nc = cfg.get("news") or {}
    if not nc.get("enabled"):
        return None
    ts = times_ns(nc["file"])
    k = np.searchsorted(ts, t_ns, "right")
    return int(ts[k] - nc["before_min"] * 60e9) if k < len(ts) else None
