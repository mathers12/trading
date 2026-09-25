"""Príkazový riadok:

  python -m smc_bot fetch                      stiahne/aktualizuje M1 dáta z OANDA
  python -m smc_bot backtest [--charts 20]     backtest + report do reports/
  python -m smc_bot compare                    porovná základný model s pridanými filtrami
  python -m smc_bot scan                       živé skenovanie a upozornenia na Telegram
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import engine, notify, report, stats
from .bias import label as bias_label
from .config import apply_overrides, load_config
from .context import build_context
from .data import load_m1, load_recent


def _args():
    p = argparse.ArgumentParser(prog="smc_bot")
    p.add_argument("command", choices=["fetch", "backtest", "compare", "scan", "research"])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--source", help="oanda | sample | cesta k súboru")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--out", help="priečinok reportu")
    p.add_argument("--charts", type=int, default=0, help="koľko grafov obchodov vykresliť")
    p.add_argument("--set", action="append", default=[], metavar="KĽÚČ=HODNOTA", help="napr. filters.require_h4_crt=true")
    p.add_argument("--state", default="state/scan_state.json")
    p.add_argument("--split", default="2025-07-01", help="research: začiatok out-of-sample obdobia")
    p.add_argument("--test-notify", action="store_true", help="scan: pošli na Telegram aj stavovú správu (test)")
    return p.parse_args()


def _load(cfg, a):
    m1 = load_m1(cfg, a.source, a.start, a.end)
    if not len(m1):
        raise SystemExit("Žiadne dáta.")
    print(f"Dáta: {len(m1)} M1 sviečok, {m1.index[0]:%Y-%m-%d} – {m1.index[-1]:%Y-%m-%d}")
    return m1


def cmd_backtest(cfg, a):
    m1 = _load(cfg, a)
    ctx = build_context(m1, cfg)
    res = engine.run(ctx)
    trades = report.to_frame(res["setups"])
    out = Path(a.out or f"reports/{pd.Timestamp.now():%Y%m%d_%H%M%S}")
    meta = {"start": f"{m1.index[0]:%Y-%m-%d}", "end": f"{m1.index[-1]:%Y-%m-%d}", "source": a.source or cfg["data"]["source"]}
    summ = report.write_report(out, trades, res["rejected"], cfg, meta, ctx.frames["M5"], a.charts)
    print("\n".join(f"  {k}: {v}" for k, v in summ.items()))
    print(f"Report: {out}/summary.md")


VARIANTS = [
    ("základný model", []),
    ("bez bias filtra", ["bias.filter=false"]),
    ("bias iba D1 (bez fallbacku)", ["bias.fallback=[]"]),
    ("+ iba killzones", ["filters.require_killzone=true"]),
    ("+ H4 CRT", ["filters.require_h4_crt=true"]),
    ("+ HTF POI (OB/FVG)", ["filters.require_htf_poi=true"]),
    ("+ volume profile", ["filters.require_vp=true"]),
    ("+ inducement", ["filters.require_inducement=true"]),
    ("+ premium/discount", ["filters.require_premium_discount=true"]),
    ("vstup na CE (50 % FVG)", ["entry.price=ce"]),
    ("TP = najbližšia likvidita", ["target.mode=nearest"]),
]


def cmd_compare(cfg, a):
    m1 = _load(cfg, a)
    ctx = build_context(m1, cfg)
    rows = []
    for name, sets in VARIANTS:
        ctx.cfg = apply_overrides(cfg, sets)
        s = stats.summary(report.to_frame(engine.run(ctx)["setups"]), cfg["risk"]["risk_per_trade_pct"])
        rows.append({"variant": name, **{k: s.get(k, 0) for k in ("obchodov", "úspešnosť_%", "priemer_R", "profit_factor", "spolu_R", "max_drawdown_R")}})
        print(f"  {name}: {rows[-1]}")
    df = pd.DataFrame(rows).set_index("variant")
    out = Path(a.out or f"reports/compare_{pd.Timestamp.now():%Y%m%d_%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "compare.csv")
    (out / "compare.md").write_text(
        f"# Porovnanie variantov – {cfg['instrument']} {m1.index[0]:%Y-%m-%d} – {m1.index[-1]:%Y-%m-%d}\n\n"
        + report._md_table(df), encoding="utf-8")
    print(f"Report: {out}/compare.md")


def cmd_scan(cfg, a):
    """Stiahne posledné dni, prejde ich rovnakým kódom ako backtest a pošle nové upozornenia."""
    if a.source:
        m1 = load_m1(cfg, a.source, a.start, a.end)
    else:
        m1 = load_recent(cfg, cfg["live"]["lookback_days"])
    ctx = build_context(m1, cfg)
    res = engine.run(ctx, simulate_trades=False, collect_alerts=True)
    state_path = Path(a.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    last = int(state.get("last_ns", 0))
    now_ns = int(ctx.m5["close_ns"][-1])
    min_ns = now_ns - cfg["live"]["alert_max_age_minutes"] * 60 * 10**9
    new = [x for x in res["alerts"] if x["ns"] > last and x["ns"] >= min_ns]
    # stage 1 (sweep) posielame iba v smere biasu a v povolenom čase, aby nebol spam
    new = [x for x in new if x["stage"] == "signal" or (x["bias_aligned"] and x["in_window"])]
    for x in new:
        ts = pd.Timestamp(x["ns"], tz="UTC").tz_convert("America/New_York")
        notify.send(f"{x['text']}\n⏱ {ts:%d.%m. %H:%M} NY")
    last_bar = pd.Timestamp(now_ns, tz="UTC")
    print(f"Nových upozornení: {len(new)} (posledná M5 sviečka {last_bar:%Y-%m-%d %H:%M} UTC)")
    if a.test_notify:
        b, bsrc, d1b, w1b = engine.effective_bias(ctx, len(ctx.m5["close_ns"]) - 1)
        text = (f"✅ SMC bot funguje\n{cfg['instrument']}: {ctx.m5['close'][-1]:.5f}\n"
                f"Posledná M5 sviečka: {last_bar.tz_convert('America/New_York'):%d.%m. %H:%M} NY\n"
                f"Bias: {bias_label(b)} ({bsrc or '-'}) | D1 {bias_label(d1b)} | W1 {bias_label(w1b)}\n"
                f"Dáta: {len(m1)} M1 sviečok od {m1.index[0]:%d.%m.%Y}\n"
                f"Nových upozornení teraz: {len(new)}")
        if not notify.send(text):
            raise SystemExit("Testovaciu správu sa nepodarilo poslať na Telegram (skontroluj TELEGRAM_BOT_TOKEN a TELEGRAM_CHAT_ID).")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_ns": max([last] + [x["ns"] for x in new])}))


def cmd_research(cfg, a):
    from . import research

    m1 = _load(cfg, a)
    ctx = build_context(m1, cfg)
    df = research.run_grid(ctx, cfg, a.split)
    out = Path(a.out or f"reports/research_{pd.Timestamp.now():%Y%m%d_%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "research.csv", index=False)
    top = research.rank(df).head(30)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print("\nTOP 30 podľa in-sample (IS), vedľa výsledok na OOS:")
    print(top.to_string(index=False))
    both = df[(df["IS_n"] >= 40) & (df["OOS_n"] >= 25)].copy()
    both["min_avgR"] = both[["IS_avgR", "OOS_avgR"]].min(axis=1)
    robust = both.sort_values("min_avgR", ascending=False).head(15)
    print("\nNajrobustnejšie (najlepší horší z IS/OOS):")
    print(robust.to_string(index=False))
    md = [f"# Research {cfg['instrument']} – OOS od {a.split}\n", "## Top 30 podľa IS\n",
          report._md_table(top.set_index("liq")), "\n## Najrobustnejšie\n", report._md_table(robust.set_index("liq"))]
    (out / "research.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Report: {out}/research.md")


def main():
    a = _args()
    cfg = load_config(a.config, a.set)
    if a.command == "fetch":
        _load(cfg, a)
    elif a.command == "backtest":
        cmd_backtest(cfg, a)
    elif a.command == "compare":
        cmd_compare(cfg, a)
    elif a.command == "research":
        cmd_research(cfg, a)
    else:
        cmd_scan(cfg, a)


if __name__ == "__main__":
    main()
