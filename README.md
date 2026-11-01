# SolBot – demo bot do handlu Solaną (fikcyjne pieniądze)

Bot obserwuje cenę SOL/USDC, uczy się na ostatnim tygodniu notowań, którą strategię stosować,
i handluje **na niby** (paper trading) na żywych cenach, z doliczaniem **wszystkich opłat transakcyjnych**.

> ⚠️ To narzędzie demonstracyjne i badawcze. Nie wysyła żadnych prawdziwych transakcji i nie jest poradą inwestycyjną.
> Wynik z przeszłości (zwłaszcza z jednego tygodnia) nie gwarantuje wyników w przyszłości.

Wymagania: tylko **Python 3.10+**, bez dodatkowych bibliotek i bez kluczy API.

## Szybki start

```bash
python -m solbot fees                # modele opłat i koszt pełnego cyklu kup+sprzedaj
python -m solbot learn --refresh     # pobierz 7 dni świec 1-min i znajdź najlepszą strategię po opłatach
python -m solbot backtest --compare-fees   # sprawdź ją na całym tygodniu + porównanie giełd
python -m solbot run                 # bot demo na żywo + panel: http://127.0.0.1:8050
```

`run` przyjmuje m.in. `--cash 1000` (kapitał startowy), `--interval 10` (co ile sekund sprawdzać cenę),
`--relearn-hours 24` (co ile godzin uczyć się od nowa), `--reset` (nowe konto demo).
Stan konta jest zapisywany w `data/` – po restarcie bot kontynuuje.

Testy: `python -m unittest -v`

## Tryb własnej obserwacji (bot uczy się tylko z tego, co sam zobaczył)

```bash
python -m solbot observe          # obserwacja + nauka + handel demo, panel: http://127.0.0.1:8050
python -m solbot learn --own      # ręczna nauka na własnych obserwacjach
python -m solbot backtest --own   # backtest nauczonej strategii na własnych obserwacjach
```

Co 10 sekund bot sam obserwuje rynek i zapisuje to w `data/live/ticks.csv` (świece 1-min w `data/live/live_bars.csv`):
* cenę SOL (Jupiter) i zmianę 24 h,
* **realną cenę kupna i sprzedaży SOL** – notowania swapu Jupitera dla pozycji ~1000 USD (zawierają opłatę puli i wpływ na cenę),
* wskaźniki rynkowe: BTC, ETH, JUP, BONK (strategia `market_lead` sprawdza, czy ruch BTC/ETH wyprzedza SOL).

Nie korzysta z żadnej historii z zewnątrz. Dopóki nie zbierze 6 h obserwacji i nie znajdzie strategii zyskownej po opłatach
na danych treningowych **i** testowych – tylko obserwuje. Uczy się od nowa co 6 h na wszystkich swoich obserwacjach.
Transakcje demo wykonuje po obserwowanych cenach kupna/sprzedaży + 0,03% zapasu na opóźnienie + opłata sieci Solana
(profil opłat `jupiter_live`).

## Jak bot „uczy się” handlować

1. **Obserwacja** – bot co kilka sekund pobiera cenę (Jupiter Price API, awaryjnie CoinGecko), zapisuje ją do
   `data/ticks.csv` i składa w świece 1-min (`data/live_bars.csv`).
2. **Nauka** (`learn`, a w trybie `run` automatycznie co 24 h) – na ostatnich 7 dniach (historia z GeckoTerminal +
   własne obserwacje) losowo przeszukuje setki kombinacji parametrów 5 strategii:
   * `dip_buy` – łapanie szybkich spadków i odbić,
   * `mean_reversion` – powrót do średniej (z-score / wstęgi Bollingera),
   * `rsi` – wyprzedanie / wykupienie,
   * `breakout` – wybicie ponad maksimum (momentum) z trailing stopem,
   * `ema_cross` – przecięcie średnich (trend).

   Każda ma take-profit, stop-loss, trailing stop, maks. czas trzymania i przerwę po transakcji.
3. **Ochrona przed przeuczeniem** – dane dzielone są chronologicznie: 70% trening, 30% test.
   Wybierana jest konfiguracja dobra na *obu* częściach. Take-profit musi z zapasem pokrywać koszt opłat.
   **Jeśli nic nie zarabia po opłatach, bot przechodzi w tryb obserwacji i nie otwiera pozycji.**

## Opłaty uwzględnione w symulacji

Każda transakcja kupna i sprzedaży płaci:

| składnik | `jupiter` (domyślny, DEX on-chain) |
|---|---|
| opłata puli / prowizja giełdy | 0,05% |
| opłata platformy (np. portfel, frontend) | 0% (profil `wallet_swap`: 0,85%) |
| poślizg / price impact / spread | 0,05% |
| opłata bazowa sieci Solana | 5 000 lamportów / podpis |
| priority fee | 100 000 lamportów / tx |
| tip Jito (opcjonalnie) | 0 |
| jednorazowo: rent za konto tokena USDC | 0,00203928 SOL |

Pełny cykl kup+sprzedaj kosztuje ok. **0,2%** – ruchy mniejsze niż to są dla bota nieopłacalne.
Profile dla porównania: `binance` (0,1% taker), `kraken` (0,4%), `coinbase` (0,6%), `wallet_swap`, `zero`.
Stawki to szacunki – zweryfikuj u swojego dostawcy i zmień je w `solbot/fees.py`
albo w sekcji `fees` pliku `data/best_config.json`.

## Realizm backtestu

* Sygnał z zamknięcia świecy jest wykonywany dopiero na **następnej** świecy (bez zaglądania w przyszłość).
* Domyślnie (`--fill close`) TP/SL i transakcje liczone są po cenach zamknięcia – to celowo konserwatywne.
  Minutowe świece z pojedynczej puli DEX zawierają „szum” odbić bid/ask; w trybie optymistycznym
  (`--fill intrabar`) na danych puli Orca strategie „zarabiały” nawet +70%/tydzień, co jest artefaktem danych,
  a nie realną okazją. Dlatego domyślnym źródłem jest mniej zaszumiona pula SOL/USDC na Meteora.

## Wyniki na tygodniu 25.10–01.11.2026 (model opłat `jupiter`)

* SOL: 102,06 → 95,63 USD (**kup i trzymaj: −6,3%**)
* Najlepsza: `breakout` (lookback 120 min, TP 1,5%, SL 0,5%, trailing 1%):
  trening +1,76%, test +0,99%, cały tydzień **+2,78%** przy 15 cyklach, trafność 40%, opłaty 29,23 USD, max obsunięcie 3,97%.
* Ta sama strategia z innymi opłatami: Binance +2,54%, Kraken −6,15%, Coinbase −11,37%, swap w portfelu −19,40%.
  **Opłaty decydują o wszystkim** – przy częstym handlu tylko tanie wykonanie ma sens.
* 15 transakcji to za mało, by mówić o pewnej przewadze – dlatego bot dalej obserwuje rynek i co dobę uczy się od nowa.

## Struktura

```
solbot/
  fees.py        model opłat + profile
  broker.py      symulowany rachunek (fikcyjne pieniądze), księgowanie opłat
  data.py        historia (GeckoTerminal), cena live (Jupiter/CoinGecko), świece
  indicators.py  wskaźniki (średnie, odchylenie, RSI, EMA)
  strategies.py  strategie i przestrzenie parametrów
  backtest.py    silnik backtestu
  optimize.py    nauka: przeszukiwanie parametrów + walidacja na danych testowych
  paper.py       bot demo na żywo (obserwacja + handel na niby + okresowa nauka)
  dashboard.py   panel WWW (static/index.html), działa offline
tests/           testy jednostkowe
data/            dane i konfiguracja (sol_1m_7d.csv, best_config.json, learn_report.json)
```
