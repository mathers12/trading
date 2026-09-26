# Analýza cudzích obchodov XAU/USD (august 2026)

Zdroj: `trades_2026-09-26_22.xlsx`, 308 riadkov, časy v UTC. 6 riadkov nie je zlato (iné symboly, zanedbateľné P/L) → analyzovaných **302 obchodov**.
Kontext ceny: Dukascopy M1 XAU/USD 2026-07-27 – 2026-08-31 (98 % vstupných cien sedí do M1 sviečky).
Zlato v auguste: 4 075 → 4 697 → 4 452, silný býčí trend (+~9 %).

## Čísla
| | hodnota |
|---|---|
| P/L | +50 900 $ (1 bod = 25 $ pri 0,25 lota) |
| PF / úspešnosť | 2,13 / 58 % |
| BUY / SELL | 255 (PF 2,66, +57 k$) / 47 (úspešnosť 43 %, −6,3 k$) |
| „zhluky“ (vstupy do 30 min, rovnaký smer) | 113 rozhodnutí, 67 jednotlivých, 9 zhlukov po 7–19 vstupoch |
| 3 najlepšie dni (5., 10., 19. 8.) | +68 k$, **ostatné dni spolu −17 k$** |
| 10 najlepších obchodov | +39 k$ z 51 k$ |
| obchody držané > 8 h | 49 ks, +66 k$; všetky kratšie spolu −16 k$ |
| korelácia denného P/L s denným pohybom v smere obchodu | 0,70 |

## Aký typ stratégie to je
**Diskrečný trendový „momentum + pyramidovanie“ na zlate**, nie mechanický SMC setup:
1. **Smer**: takmer vždy long v býčom trhu (HTF trend). Shorty proti trendu stratové.
2. **Čas**: London open 06–07 UTC (08–09 SK) a NY open 13–14 UTC (15–16 SK). London: 91 obch., PF 4,75; NY: 190 obch., slabé.
3. **Vstup**: väčšinou v hornej časti rozsahu posledných 60 min (medián 0,78) – kupuje silu/pokračovanie, nie hlboký pullback.
   Najlepšie vstupy **skoro v dni** (rozsah dňa zatiaľ < ~50 $: úspešnosť 72 %, priemer +25 bodov); po väčšom rozsahu strata.
   Deň, keď cena už vybrala včerajšie minimum (sweep PDL) a kupuje sa: priemer +13 bodov vs. +2.
   Naopak vybratie ázijského minima pred vstupom: úspešnosť 37 %, strata – „sweep Ázie“ tu nefunguje.
4. **Pridávanie do pozície**: keď pohyb ide, otvorí 5–19 ďalších obchodov po 0,25 lota v priebehu 20–70 min (62 % pridaní vyššie = pyramída).
5. **Riadenie**: počiatočný SL ~10 $ (100 pipov), rýchlo posun na **BE + 0,1** (88 obchodov skončilo na nule), TP ďaleko
   (okrúhle čísla 4 297, 4 530, 4 681 → RR 1:8 a viac) alebo ručné zavretie; výherné obchody držané hodiny až cez noc.
   Stratové ručne zatvorené okolo −20 bodov. Priemerná výhra 43 bodov vs. strata 23.

Výhoda je teda v **smere dňa + veľkosti pozície na trendových dňoch + nechaní ziskov bežať**, nie v presnosti vstupov
(MFE/MAE za 4 h medián 16/15 bodov, iba 39 % vstupov má MFE > 2 × MAE). Náhodné vstupy s rovnakým smerom a dĺžkou držania: +2,7 bodu/obchod, skutočné +6,6.

## Obmedzenia
- **Iba 1 mesiac a jeden trhový režim** (silný býčí trend). Efektívna vzorka sú ~3 trendové dni, nie 308 obchodov;
  bez nich je výsledok −17 k$. Štatisticky to nedokazuje výhodu.
- S uzavretím o 19:00 SK (17:00 UTC) by bol výsledok +37,9 k$, PF 1,92 (spätne, s tými istými vstupmi).
- Filtre „iba BUY“ / „iba London“ vyzerajú výborne (PF 2,7 / 4,8), ale sú odvodené z tých istých dát – nutné overiť na histórii.

## Mechanický návrh na backtest (XAU/USD 2020–2026, IS/OOS)
- Bias: D1/W1 trend (napr. close nad EMA50 D1 alebo repo `bias.py`), obchoduje sa iba v smere.
- Okno: 08–12 SK (London), voliteľne 15–16 SK.
- Podmienka: rozsah dňa do vstupu malý (napr. < 0,5 × ATR D1); voliteľne sweep PDL/PDH v dni.
- Spúšťač: M5 MSS / prerazenie 60-min maxima v smere biasu.
- SL 10 $ (alebo za M5 swing), BE po +1R, pridať 1–3 pozície pri každom +1R, runner do 19:00 SK alebo TP na okrúhlom čísle / 3–5R.
