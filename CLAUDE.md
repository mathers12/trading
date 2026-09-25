# CLAUDE.md

SMC/ICT signálny bot a backtester pre EUR/USD. Bot obchody **nezadáva**, iba posiela upozornenia na Telegram.
Používateľ je trader (day trading). Komunikuj s ním **po slovensky**, texty v kóde, reportoch a commitoch tiež po slovensky.
Časy v odpovediach uvádzaj v stredoeurópskom čase (Europe/Bratislava), nie v UTC.

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
- **Používateľ chce rýchle testy: vždy iba vzorka 1 mesiaca** (lokálne, napr. `--start 2025-07-01 --end 2025-08-01 --set smt.instrument=null`),
  nie celé roky na Actions. Cieľ na vzorke: ≥ 1 obchod denne (~20/mesiac), úspešnosť, PF, R.
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
- **Kolo 2026-09-25 (stratégie 1–4, IS 2020–2023 / OOS 2024+ / VER 2025-07+, `research --grid poi|asia_bo|asia_fo|va --verify`):**
  - POI/supply-demand (GBP): 0,04–0,4 obchodu/deň, PF ~0,85–1,05; najlepšie H1 zóna + trh + sweep v zóne PF 1,45/1,08/1,02 (iba ~46 obchodov) → bez výhody.
  - Breakout Ázie: EUR 08–13, trh, SL na opačnej strane, 3R: PF 1,02/1,05/1,07 pri ~0,9 obchodu/deň; GBP s D1 smerom, SL stred, 3R: 1,08/1,04/1,07 (0,25–0,4/deň) → tesne nad nulou, nie cieľ.
  - Judas/turtle soup (GBP): PF 0,9–1,13 v IS, OOS < 1 → neobstál.
  - Návrat do value area (GBP): ~0,06 obchodu/deň, PF < 1 → neobstál (podozrivo málo signálov, skontrolovať podmienku).
  - EUR behy POI/Judas/VA dobiehali (sťahovanie EUR 2020–23 na Actions).
- **HTF pullback model** (dokument používateľa: H1 BOS → pullback do golden pocketu + FVG H1/M15 → inducement → M1/M5 MSS; `smc_bot/htf.py`,
  `liquidity.external=["htf_pb"]`). Vzorka EUR/USD 07/2025: iba prvý dotyk = 1–5 obchodov/mesiac.
  Uvoľnené (M1 MSS, fib 0,5–0,79, každý návrat do zóny `htf.max_touches=99`, TP 2R): ~1 obchod/deň, úspešnosť 36–38 %, PF 1,1–1,2.
  **Top-down** (`htf.align`: W1/D1 = bias dvoch sviečok, H4/H1/M15 = smer posledného BOS; pullback iba v smere vyšších TF),
  pullbacky naraz na H1 aj M15 (`htf.timeframe=["H1","M15"]`), M1 MSS, fib 0,5–0,79, TP 2R, max 4 obchody/deň, viac pozícií naraz:
  **07/2025: 23 obchodov (1,05/deň), úspešnosť 57 %, PF 2,45, +14,5R, obchod v 14/22 dňoch.** Pridanie M5 pullbackov alebo swing 1 = viac obchodov, horší PF.
  Ladené na jednom mesiaci → **overenie bez úprav: 08/2025 15 obchodov, 20 %, PF 0,37, −6,9R; 03/2025 10 obchodov, 30 %, PF 0,72, −1,9R → neobstálo**
  (júl bol náhoda/preučenie na jednom mesiaci).
  Potvrdenia na 3 mesiacoch spolu (03+07+08/2025, `news.enabled` = 2 h pred / 30 min po správach z `calendar/news.csv`):
  správy −1 obchod (PF 1,20→1,25); H4 CRT PF 1,62 (25 obch.); okno LO+NY PF 1,49; **správy + H4 CRT + LO+NY: 19 obchodov (0,3/deň), 53 %, PF 2,12**,
  ale 11 z 19 obchodov je z júla, august PF ~1,0. Premium/discount a vstup na 50 % škodia; htf_idm a poi_sweep sú príliš voľné (nemenia nič).
- Metodika pre ďalšie kolá: ladiť na 2020–2023 (+ časť 2024), overovať na 2025-07+; výsledky vždy s počtom obchodov/deň.

## Ďalšie stratégie na otestovanie (dohodnuté s používateľom)
1. **POI / supply-demand zóny (priorita, definícia od používateľa):**
   - zóna = order block (supply/demand) na **H4, H1 alebo M15**,
   - platná iba ak z nej vznikol **displacement** a **prerazenie štruktúry nechalo FVG**,
   - obchoduje sa **iba prvý dotyk**, druhý dotyk je neplatný,
   - ideálne, keď v zóne nastane **sweep likvidity** a **inducement** (tagy, potom otestovať, či pomáhajú),
   - vstup po M5 MSS v zóne, SL za zónou/extrémom, TP 2R alebo opačná likvidita; bias D1, premium/discount ako tag.
2. Londýnsky breakout ázijského rozsahu (v smere D1/H4 trendu).
3. Falošné prerazenie ázijského rozsahu v Londýne (Judas / turtle soup), TP na opačnej strane rozsahu.
4. Návrat k hodnote (VWAP / value area predchádzajúceho dňa) v dňoch bez trendu.
Každú overiť: ladenie 2020–2023, overenie 2025-07+, EUR/USD aj GBP/USD, výsledky s počtom obchodov/deň.

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
