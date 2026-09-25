# SMC signálny bot a backtester (EUR/USD)

Bot sleduje EUR/USD a hľadá day-tradingové setupy podľa Smart Money / ICT konceptov.
Keď nájde setup, pošle upozornenie na Telegram. Ten istý kód robí aj **backtest** na
historických M1 bid/ask dátach z **Dukascopy** (zadarmo, bez registrácie): zistí, ktoré potenciálne obchody by prešli (TP) a ktoré nie (SL),
a spraví k tomu štatistiku.

> Bot obchody **nezadáva**. Upozorní ťa a rozhodnutie je na tebe.

## Stratégia (ako ju bot vyhodnocuje)

1. **Bias (D1, W1).** Počíta sa z dvoch posledných uzavretých sviečok (C1, C2):
   - **bullish:** C2 sa uzavrie nad high C1, alebo vyberie low C1 a uzavrie sa späť v rozsahu,
   - **bearish:** C2 sa uzavrie pod low C1, alebo vyberie high C1 a uzavrie sa späť v rozsahu,
   - **avoid:** inside bar, alebo C2 vyberie obe strany a uzavrie sa vnútri.

   Keď D1 nedá smer, použije sa W1. Ak ani ten nie, bias je smer k **najbližšej
   nevybranej externej likvidite** (PDH/PDL, PWH/PWL, ázijský high/low).
2. **Vybratie likvidity (povinné).** Bez vybratia likvidity nie je setup. Počíta sa externá
   likvidita (PWH/PWL, PDH/PDL, Ázia, H1/M15 swing high/low) aj interná (dotyk H1 FVG).
3. **MSS na M5.** Uzavretie za posledným swingom, z ktorého vznikol pohyb do sweepu.
   Musí mať **displacement**: silnú sviečku (telo ≥ 1,5 × ATR) alebo pohyb, ktorý nechal FVG.
4. **Vstup.** Limitka na FVG vytvorenom v tomto pohybe. Ak FVG nie je, vstupuje sa na order blocku.
5. **SL** za extrémom sweepu (+1 pip). Neposúva sa.
6. **TP** na najbližšej opačnej likvidite (high/low), ktorá dáva **RR aspoň 1:2**.
7. **Konfluencie.** Pri každom obchode sa zaznamenajú, aby sa dalo zmerať, či pomáhajú:
   H4 CRT, HTF POI (OB/FVG na H4/H1/M15), volume profile (POC, VAH, VAL, naked POC),
   inducement, premium/discount, killzone, obchod v smere W1 a či TP je likvidita daily biasu.
   Každú z nich sa dá v `config.yaml` zapnúť ako povinnú.

Všetky parametre (dĺžka swingov, veľkosť FVG, killzones, limity…) sú v [`config.yaml`](config.yaml)
s komentármi.

### Pravidlá backtestu (konzervatívne)
- Stratégia vidí iba uzavreté sviečky, takže nenazerá do budúcnosti (overuje to test `test_no_lookahead`).
- Obchody sa simulujú na **M1 bid/ask** dátach: long sa plní za ask a zatvára za bid, **spread je teda zahrnutý**.
- Ak v jednej M1 sviečke padne SL aj TP, počíta sa **SL**.
- Ak cena dosiahne TP skôr, ako sa limitka naplní, obchod sa počíta ako *ušlý* (neráta sa do výsledku).
- O 16:00 NY sa otvorený obchod zatvorí (day trading). Limitka platí 1 hodinu.
- Najviac 2 obchody a 2 straty denne, vždy len jeden obchod naraz.

## Nastavenie (raz)

**Na backtest netreba nič.** História sa sťahuje z Dukascopy bez registrácie a tokenu.

Na živé upozornenia pridaj v repozitári (*Settings → Secrets and variables → Actions →
New repository secret*) tieto tri secrets:

| Name | Hodnota | Kde ju získaš |
|---|---|---|
| `TWELVEDATA_API_KEY` | API kľúč | zaregistruj sa na [twelvedata.com](https://twelvedata.com) (bezplatný plán), kľúč nájdeš v *Dashboard → API Keys* |
| `TELEGRAM_BOT_TOKEN` | napr. `7412345678:AAH…` | v Telegrame napíš [@BotFather](https://t.me/BotFather) príkaz `/newbot` |
| `TELEGRAM_CHAT_ID` | číslo, napr. `123456789` | napíš svojmu botovi správu a otvor `https://api.telegram.org/bot<TOKEN>/getUpdates`, hodnota `"chat":{"id": …}` |

Twelve Data slúži iba na doplnenie dnešných sviečok, lebo Dukascopy zverejňuje deň až po jeho skončení.
Ak máš OANDA účet s prístupom k API, môžeš v `config.yaml` nastaviť `data.source: oanda`
a pridať secret `OANDA_API_TOKEN`. Tokeny nikdy nedávaj priamo do kódu.

## Používanie cez GitHub (bez inštalácie)

- **Backtest:** *Actions → Backtest → Run workflow*. Zadaj obdobie a spusti.
  Výsledok uvidíš priamo v súhrne behu. Kompletný report (CSV so všetkými obchodmi,
  grafy obchodov, equity krivka a porovnanie variantov) si stiahneš ako artefakt `backtest-report`.
- **Živé upozornenia:** workflow *Skenovanie trhu* beží automaticky Po–Pi každých 10 minút
  od 06:00 do 16:59 UTC. Pošle dva typy správ:
  - 🟡 *sweep likvidity v smere biasu, čakám na MSS*,
  - 🟢 *setup: vstup, SL, TP, RR a konfluencie*.

> GitHub Actions má pri **súkromnom** repozitári 2000 minút mesačne zadarmo (verejný repozitár
> je bez limitu). Skenovanie každých 10 minút v uvedených hodinách spotrebuje približne 1500 minút.

## Používanie na počítači

```bash
pip install -r requirements.txt
export TWELVEDATA_API_KEY=...              # iba pre scan; Windows: set TWELVEDATA_API_KEY=...

python -m smc_bot backtest --start 2024-01-01 --charts 20   # report v reports/<dátum>/
python -m smc_bot compare  --start 2024-01-01               # porovnanie variantov
python -m smc_bot scan                                      # jedno živé skenovanie

# na umelých dátach (iba na test, že všetko beží)
python -m smc_bot backtest --source sample

# zmena nastavení bez úpravy súboru
python -m smc_bot backtest --set filters.require_h4_crt=true --set target.min_rr=3
```

Dáta sa ukladajú do `data/` a pri ďalšom spustení sa dopĺňajú iba chýbajúce dni.
Prvé stiahnutie dvoch rokov z Dukascopy trvá niekoľko minút.

### Čo obsahuje report
- `summary.md` obsahuje úspešnosť, priemerné R, profit factor, max. drawdown, najdlhšiu sériu strát
  a **rozpad výsledkov** podľa seansy, dňa, typu likvidity, biasu a každej konfluencie.
- `trades.csv` je zoznam všetkých setupov: vstup, SL, TP, výsledok, R a konfluencie.
- `rejected.csv` sú setupy, ktoré neprešli pravidlami, aj s dôvodom (napr. RR pod 2, proti biasu).
- `equity.png` je vývoj účtu v R. `charts/` obsahuje graf každého obchodu so sweepom, MSS, vstupom, SL a TP.
- `compare.md` porovnáva základný model s pridanými filtrami a ukazuje, ktoré konfluencie naozaj pomáhajú.

## Štruktúra projektu

| súbor | čo robí |
|---|---|
| `smc_bot/dukascopy.py` | Dukascopy M1 bid/ask história + Twelve Data na dnešné sviečky |
| `smc_bot/data.py` | výber zdroja, OANDA v20 API, cache, umelé dáta |
| `smc_bot/timeframes.py` | M1 → M5/M15/H1/H4/D1/W1 (deň začína 17:00 NY ako na OANDA) |
| `smc_bot/structure.py` | ATR, swingy, FVG, order blocky |
| `smc_bot/bias.py` | daily/weekly bias |
| `smc_bot/volume_profile.py` | POC, VAH, VAL, naked POC (z tick volume) |
| `smc_bot/context.py` | likvidita, POI zóny, predpočítanie |
| `smc_bot/engine.py` | hľadanie setupov (sweep → MSS → vstup) |
| `smc_bot/backtest.py` | simulácia obchodu na M1 bid/ask |
| `smc_bot/stats.py`, `report.py` | štatistika, report, grafy |
| `smc_bot/notify.py` | Telegram |

## Upozornenie
Forex nemá centrálnu burzu, takže volume z Dukascopy (alebo OANDA) je objem iba u jedného
brokera, nie celého trhu. Volume profile je preto aproximácia.
Výsledky backtestu nie sú zárukou budúcich výsledkov. Nový setup testuj najprv na demo účte.
