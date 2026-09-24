"""Výstupy backtestu: CSV so všetkými obchodmi, summary.md, equity graf a grafy obchodov."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import stats  # noqa: E402

TRADE_COLUMNS = [
    "signal_time", "direction", "status", "result", "r", "rr", "entry", "sl", "tp", "sl_pips", "entry_type",
    "sweep_kind", "swept", "sweep_level", "sweep_time", "extreme", "mss_level", "target_kind",
    "fill_time", "exit_time", "exit_price", "session", "bias", "bias_source", "d1_bias", "w1_bias",
    "bias_aligned", "w1_aligned", "target_is_bias_dol", "displacement", "strong_body", "h4_crt", "htf_poi",
    "vp", "inducement", "premium_discount",
]

STATUS_SK = {"FILLED": "vstup", "NOT_FILLED": "nenaplnené", "MISSED": "TP pred vstupom", "CANCELLED_EOD": "zrušené (koniec dňa)"}


def to_frame(setups: list[dict]) -> pd.DataFrame:
    if not setups:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    df = pd.DataFrame(setups)
    for src, dst in (("fill_ns", "fill_time"), ("exit_ns", "exit_time")):
        df[dst] = pd.to_datetime(df[src].astype("float").astype("Int64"), utc=True) if src in df else pd.NaT
    return stats.enrich(df)


def _md_table(df: pd.DataFrame) -> str:
    if df is None or not len(df):
        return "_žiadne dáta_\n"
    cols = [df.index.name or ""] + list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for idx, row in df.iterrows():
        lines.append("| " + " | ".join([str(idx)] + [str(v) for v in row.to_numpy()]) + " |")
    return "\n".join(lines) + "\n"


def write_report(out_dir: str | Path, trades: pd.DataFrame, rejected: list[dict], cfg: dict, meta: dict,
                 m5: pd.DataFrame | None = None, charts: int = 0) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = [c for c in TRADE_COLUMNS + ["weekday", "month"] if c in trades.columns]
    trades[cols].to_csv(out / "trades.csv", index=False)
    pd.DataFrame(rejected).to_csv(out / "rejected.csv", index=False)
    summ = stats.summary(trades, cfg["risk"]["risk_per_trade_pct"])

    md = [f"# Backtest {cfg['instrument']}\n",
          f"Obdobie: **{meta.get('start')} – {meta.get('end')}** · zdroj dát: **{meta.get('source')}**\n"]
    if meta.get("source") == "sample":
        md.append("> ⚠️ Umelé dáta: výsledky slúžia iba na kontrolu, že program beží, o stratégii nič nehovoria.\n")
    md.append("## Súhrn\n")
    md.append("| metrika | hodnota |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in summ.items()) + "\n")
    if len(trades) and (trades["status"] == "FILLED").any():
        md.append("![equity](equity.png)\n")
    md.append("## Rozpad výsledkov\n")
    md.append("Tu vidno, ktoré konfluencie pridávajú výhodu (porovnaj **priemer_R** pri áno a nie).\n")
    for col, title in stats.BREAKDOWNS:
        if col in trades.columns:
            md.append(f"### {title}\n")
            md.append(_md_table(stats.breakdown(trades, col)))
    if rejected:
        rj = pd.DataFrame(rejected)["reason"].str.replace(r"\s*\(.*", "", regex=True).value_counts()
        md.append("## Prečo boli setupy zamietnuté\n")
        md.append(_md_table(rj.rename("počet").to_frame().rename_axis("dôvod")))
    md.append("\n## Použité nastavenia\n```yaml\n" + _yaml(cfg) + "```\n")
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")

    if len(trades) and (trades["status"] == "FILLED").any():
        _equity(trades, out / "equity.png")
    if charts and m5 is not None:
        _trade_charts(trades, m5, out / "charts", charts)
    return summ


def _yaml(cfg: dict) -> str:
    import yaml

    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)


def _equity(trades: pd.DataFrame, path: Path) -> None:
    t = trades[trades["status"] == "FILLED"].dropna(subset=["r"])
    eq = t["r"].cumsum().to_numpy()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t["signal_time"].dt.tz_convert(None), eq, color="#2a6fdb", lw=1.6)
    ax.fill_between(t["signal_time"].dt.tz_convert(None), eq, 0, color="#2a6fdb", alpha=0.08)
    ax.axhline(0, color="#888", lw=0.8)
    ax.set_title("Vývoj účtu (kumulatívne R)")
    ax.set_ylabel("R")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _trade_charts(trades: pd.DataFrame, m5: pd.DataFrame, out: Path, limit: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    t = trades[trades["status"] == "FILLED"].head(limit)
    for n, (_, s) in enumerate(t.iterrows(), 1):
        start = s["sweep_time"] - pd.Timedelta(hours=3)
        end = (s["exit_time"] if pd.notna(s["exit_time"]) else s["signal_time"]) + pd.Timedelta(hours=1)
        w = m5[(m5.index >= start) & (m5.index <= end)]
        if not len(w):
            continue
        fig, ax = plt.subplots(figsize=(11, 5))
        x = np.arange(len(w))
        up = w["close"] >= w["open"]
        ax.vlines(x, w["low"], w["high"], color="#555", lw=0.7)
        ax.bar(x, (w["close"] - w["open"]).abs().clip(lower=1e-6), bottom=np.minimum(w["open"], w["close"]),
               color=np.where(up, "#26a69a", "#ef5350"), width=0.7)
        for price, color, label in ((s["entry"], "#2a6fdb", "vstup"), (s["sl"], "#ef5350", "SL"), (s["tp"], "#26a69a", "TP"),
                                    (s["sweep_level"], "#f0a500", s["sweep_kind"]), (s["mss_level"], "#8e44ad", "MSS")):
            ax.axhline(price, color=color, lw=1, ls="--")
            ax.text(len(w) - 1, price, f" {label}", color=color, va="center", fontsize=8)
        for ts, mark in ((s["signal_time"], "signál"), (s["fill_time"], "vstup"), (s["exit_time"], "výstup")):
            if pd.notna(ts):
                k = int(np.searchsorted(w.index, ts)) - (1 if mark == "signál" else 0)
                ax.axvline(min(max(k, 0), len(x) - 1), color="#999", lw=0.6, ls=":")
        labels = w.index.tz_convert("America/New_York").strftime("%d.%m %H:%M")
        step = max(len(x) // 10, 1)
        ax.set_xticks(x[::step], labels[::step], rotation=30, fontsize=7)
        ax.set_title(f"#{n} {s['direction']} {s['signal_time']:%Y-%m-%d %H:%M} UTC – {s['result']} ({s['r']:+.2f}R)  čas NY")
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(out / f"trade_{n:03d}.png", dpi=110)
        plt.close(fig)
