# CLAUDE.md

SMC/ICT signálny bot a backtester pre EUR/USD. Bot obchody **nezadáva**, iba posiela upozornenia na Telegram.
Používateľ je trader (day trading). Komunikuj s ním **po slovensky**, texty v kóde, reportoch a commitoch tiež po slovensky.

## Šetrenie tokenov (dôležité)
- Odpovedaj stručne a vecne; žiadne opakovanie zadania, dlhé úvody ani zhrnutia toho, čo je vidieť v diffe.
- Nečítaj celé súbory, keď stačí časť: najprv `grep`/`Grep`, potom `Read` s `offset`/`limit`. Súbor, ktorý už poznáš, nečítaj znova.
- Výstupy príkazov skracuj (`| tail -20`, `-q` pri pytest, `head_limit`). Logy GitHub jobov čítaj s `tail_lines`, nie celé.
- Úpravy rob cielene (`Edit`), nie prepisovaním celých súborov. Viac nezávislých krokov spoj do jedného volania.
- Nespúšťaj subagentov a dlhé prieskumy bez potreby; na tento malý projekt stačí priama práca.
- Nové dlhé behy (research, backtest) spúšťaj iba s jasnou hypotézou; nekontroluj ich opakovane v krátkych intervaloch.
- Pri nejasnosti sa radšej krátko opýtaj, než robiť veľkú prácu naslepo.

## Stratégia používateľa (východisko)
Claude smie stratégiu upravovať alebo jej časti vynechať, ak dáta (IS aj OOS) ukážu, že to pomáha.
Každú takú zmenu stručne zdôvodni používateľovi a zapíš sem.
- **Bias D1** z dvoch posledných uzavretých sviečok (C1, C2), pravidlo z jeho obrázka (`smc_bot/bias.py`):
  close nad high C1 alebo sweep low C1 a návrat do rozsahu = bullish; zrkadlovo bearish; inside bar alebo vybraté obe strany = avoid.
  Keď D1 nedá smer: W1 rovnakým pravidlom, potom smer k najbližšej nevybranej externej likvidite (PDH/PDL, PWH/PWL, Ázia).
- **Bez vybratia likvidity nie je setup.** Externá (PWH/PWL, PDH/PDL, Ázia, H1/M15 swingy) aj interná (dotyk H1 FVG).
- **MSS na M5 s displacementom** (silná sviečka ≥ 1,5 × ATR alebo pohyb, ktorý nechal FVG).
- **Vstup** limitkou na FVG z tohto pohybu, inak na OB. **SL** za extrémom sweepu, **neposúva sa** (žiadny break-even).
- **TP** = opačná likvidita (najbližší high/low), **RR aspoň 1:2**. Ideálne v smere daily biasu, ale nie vždy.
- Konfluencie (H4 CRT, POI OB/FVG na H4/H1/M15, volume profile POC/VAH/VAL/nPOC, inducement, premium/discount, killzones)
  sa zaznamenávajú ako tagy; ktoré sú povinné, rozhoduje `config.yaml` → `filters`.
- Cieľ používateľa: **profit factor ≥ 2, RR 1:2, úspešnosť ≥ 50 %.**

## Dáta
- **Dukascopy** (`smc_bot/dukascopy.py`): M1 bid/ask história bez tokenu, `.bi5` = LZMA, záznamy `>u4 čas, open, close, low, high, >f4 volume`,
  mesiac v URL od 00. Aktuálny deň nie je dostupný. Server obmedzuje počet požiadaviek (429/503), preto sťahovanie opakuje a paralelizuje max. na 4.
- **Twelve Data**: iba dnešné M1 sviečky pri živom skenovaní (bez bid/ask a volume, spread sa dopočíta).
- **OANDA API** je v kóde, ale používateľ k nemu prístup nemá (EÚ pobočka).
- Obchodný deň začína **17:00 New York** (D1/H4/W1 ako OANDA), časy sessions a filtrov sú v NY čase.
- Volume je volume jedného brokera, nie celého trhu, takže volume profile je iba aproximácia.

## Pravidlá backtestu a výskumu
- Žiadne nazeranie do budúcnosti: všetko má čas, odkedy je známe (`available_ns`, `close_time`). Stráži to `tests/test_core.py::test_no_lookahead`.
- Simulácia na M1 bid/ask, konzervatívne: SL aj TP v jednej sviečke = SL, v sviečke naplnenia sa TP nepočíta, o 16:00 NY sa obchod zatvorí.
- **Výskum vždy s in-sample / out-of-sample** (predvolene OOS od 2025-07-01). Pravidlo má výhodu, iba ak drží aj na OOS.
  Nepreučovať a výsledky podávať pravdivo, aj keď cieľ nie je dosiahnutý.

## Požiadavky používateľa (aktuálne)
- Iba **EUR/USD**, prípadne **GBP/USD**. Obchodný čas **08:00–19:00 Europe/Bratislava** (signály aj uzavretie o 19:00).
- Cieľ: **aspoň 1 obchod denne**, **PF ≥ 2**, **RR 1:2**, **úspešnosť ≥ 50 %**. Repozitár zostáva verejný.

## Zistenia z výskumu (stav 2026-09-25)
Dáta: EUR/USD M1 2020-01 – 2026-09 (lokálne `data/`, ~91 MB), GBP/USD 2024+ (2020–23 sa sťahovalo).
- Základný model (sweep → M5 MSS → FVG, TP na likvidite): PF ~0,95–0,98, bez výhody.
- ~1 100 kombinácií (`research --grid ict|ict2|denny|freq`, IS 2024-01–2025-06, OOS 2025-07–2026-09):
  pomáhali OTE 70,5 %, okná Londýn 08–11 + Silver Bullet 16–17 (lokálny čas), H4 CRT, SL rezerva 3 pipy, HTF likvidita.
- **Kandidát A** (LO+SB, OTE 0,705, H4 CRT, SL +3 pipy, TP 2R): 2024–26 PF 1,46/1,83, ale na nevidených
  **2020–2023 PF 0,79 (28 %) → neobstál**. Výhoda z 2024–26 bola náhoda alebo trhový režim.
- Frekvencia vs. kvalita: viac obchodov = menšia výhoda. M5 najviac ~0,6 obchodu/deň pri PF ~1,1; **M1** až 1,8/deň, ale PF < 1 (spread).
- OTE limitka: ~60 % setupov „ušlo“ (TP pred vstupom), vypĺňajú sa hlavne horšie obchody → pridaný trhový vstup po MSS (`entry.type=market`).
- **Trhový vstup po MSS** (`research --grid market`, IS 2020–2023, OOS 2024-01+): 1–1,3 obchodu/deň (okno 08–19),
  ale PF ~1,0 v IS aj OOS, žiadna z 96 kombinácií stabilne nad PF 1,1. Samotný mechanický setup výhodu nemá.
- ML filter (`python -m smc_bot ml`, gradient boosting na vlastnostiach setupov): bez prediktívnej sily (OOS PF ~1,0–1,16).
- Nové: SMT divergencia s GBP/USD (`smt`, `filters.require_smt`), londýnsky rozsah ako likvidita (LON_H/LON_L), `structure.timeframe` M5/M1.
- Doteraz nevyskúšané nápady: SMT ako filter/vlastnosť pre ML na oboch pároch, správy (NFP/CPI/FOMC), denná volatilita (ATR),
  weekly profil, vstup na retest MSS úrovne, kombinácia trhového vstupu s H4 CRT/SMT.
- Metodika pre ďalšie kolá: ladiť na 2020–2023 (+ časť 2024), overovať na 2025-07+; výsledky vždy s počtom obchodov/deň.

## Príkazy
```bash
pip install -r requirements.txt
python -m pytest -q                                   # testy (musia prejsť pred pushom)
python -m smc_bot backtest --source sample            # rýchla kontrola na umelých dátach
python -m smc_bot backtest --start 2024-01-01 --charts 20
python -m smc_bot compare  --start 2024-01-01         # základný model vs. pridané filtre
python -m smc_bot research --start 2024-01-01 --split 2025-07-01   # mriežka kombinácií, IS/OOS
python -m smc_bot scan [--test-notify]                # živé skenovanie
python -m smc_bot backtest --set filters.require_vp=true --set target.mode=fixed_rr
```

## GitHub
- Repo `mathers12/trading` (verejné, Actions minúty bez limitu). Pracovný a predvolený branch: `claude/sharp-clarke-47h6ph`.
- Workflowy: `backtest.yml`, `research.yml`, `scan.yml` (iba ručne; cron vypnutý, kým stratégia nemá overenú výhodu – naplánované behy sa aj tak nespúšťali), `tests.yml`.
  Reporty sú v artefaktoch behu.
- Secrets: `TWELVEDATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Tokeny nikdy do kódu ani do chatu.
- Sandbox Claude session: `datafeed.dukascopy.com` je povolený, ale s limitom požiadaviek. Blob storage GitHub artefaktov nie je dostupný,
  výsledky behov čítaj cez logy jobov (GitHub MCP `get_job_logs`).

## Štruktúra
`data.py` / `dukascopy.py` (zdroje) → `timeframes.py` (resampling) → `structure.py` (swingy, FVG, OB) → `context.py` (likvidita, POI, bias, VP,
predpočítanie) → `engine.py` (setupy; long/short jedným kódom cez zrkadlenie cien x → −x) → `backtest.py` (simulácia) →
`stats.py` / `report.py` / `research.py` (vyhodnotenie). Nastavenia: `config.yaml` (predvolené hodnoty v `config.py`).
