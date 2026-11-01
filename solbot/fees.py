"""Model opłat transakcyjnych.

Każda transakcja (kupno lub sprzedaż) ponosi:
  * opłatę procentową od wartości (prowizja giełdy / opłata puli LP / opłata platformy),
  * poślizg cenowy (slippage / price impact / połowa spreadu) – gorsza cena wykonania,
  * stałą opłatę sieciową Solany w SOL (opłata bazowa + priority fee + opcjonalny tip Jito).

Wartości domyślne to szacunki – sprawdź aktualny cennik swojej giełdy / DEX-a
i nadpisz je w pliku konfiguracyjnym (sekcja "fees").
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class FeeModel:
    name: str = "jupiter"
    # Opłata procentowa od wartości transakcji (np. 0.001 = 0.1%)
    trading_fee_pct: float = 0.0005      # opłata puli / LP
    platform_fee_pct: float = 0.0        # np. opłata frontendu / Jupiter Ultra / aplikacji portfela
    # Poślizg cenowy względem ceny referencyjnej (dla kupna płacisz więcej, przy sprzedaży dostajesz mniej)
    slippage_pct: float = 0.0005
    # Opłaty sieci Solana (płacone w SOL), za każdą transakcję
    base_fee_lamports: int = 5_000       # 5000 lamportów na podpis
    priority_fee_lamports: int = 100_000 # priority fee (compute unit price * limit)
    jito_tip_lamports: int = 0           # opcjonalny tip dla walidatora Jito
    signatures: int = 1
    # Jednorazowe koszty (np. rent za konto tokena USDC) – liczone przy pierwszym zakupie
    one_time_sol: float = 0.0
    # dodatkowy koszt, gdy transakcja nie ma realnego notowania (quote) – np. opłata puli + spread
    no_quote_extra_pct: float = 0.0
    notes: str = ""

    # ---- wyliczenia ---------------------------------------------------------
    @property
    def pct_fee(self) -> float:
        return self.trading_fee_pct + self.platform_fee_pct

    @property
    def network_fee_sol(self) -> float:
        lamports = self.base_fee_lamports * self.signatures + self.priority_fee_lamports + self.jito_tip_lamports
        return lamports / LAMPORTS_PER_SOL

    def network_fee_usd(self, sol_price: float) -> float:
        return self.network_fee_sol * sol_price

    def round_trip_pct(self, notional_usd: float = 1000.0, sol_price: float = 100.0) -> float:
        """Przybliżony koszt pełnego cyklu kup+sprzedaj jako ułamek wartości pozycji."""
        pct = 2 * (self.pct_fee + self.slippage_pct + self.no_quote_extra_pct)
        fixed = 2 * self.network_fee_usd(sol_price) / max(notional_usd, 1e-9)
        return pct + fixed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FeeModel":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# Profile opłat (szacunki – zweryfikuj u swojego dostawcy!)
FEE_PROFILES: dict[str, FeeModel] = {
    # Swap SOL<->USDC przez agregator Jupiter na płynnej puli (np. Orca/Raydium CLMM)
    "jupiter": FeeModel(
        name="jupiter", trading_fee_pct=0.0005, platform_fee_pct=0.0, slippage_pct=0.0005,
        base_fee_lamports=5_000, priority_fee_lamports=100_000, jito_tip_lamports=0,
        one_time_sol=0.00203928,
        notes="DEX on-chain: opłata puli ~0.05%, poślizg ~0.05%, opłata sieci 5000 lamportów + priority fee. "
              "one_time_sol = rent za konto tokena USDC (zwrotny po zamknięciu konta).",
    ),
    # Swap przez Jupiter, ale cena wykonania brana z REALNEGO notowania (quote) obserwowanego na żywo:
    # notowanie zawiera już opłatę puli i wpływ na cenę, więc dokładamy tylko zapas na opóźnienie + sieć.
    "jupiter_live": FeeModel(
        name="jupiter_live", trading_fee_pct=0.0, platform_fee_pct=0.0, slippage_pct=0.0003, no_quote_extra_pct=0.0005,
        base_fee_lamports=5_000, priority_fee_lamports=100_000, jito_tip_lamports=0,
        one_time_sol=0.00203928,
        notes="Ceny kupna/sprzedaży z notowań Jupitera (zawierają opłatę puli i price impact) + 0.03% zapasu "
              "na opóźnienie + opłata sieci. Gdy brak notowania: cena referencyjna ± 0.08% (opłata+poślizg).",
    ),
    # Ten sam swap, ale przez aplikację/portfel pobierający opłatę serwisową (np. Phantom ~0.85%)
    "wallet_swap": FeeModel(
        name="wallet_swap", trading_fee_pct=0.0005, platform_fee_pct=0.0085, slippage_pct=0.0005,
        base_fee_lamports=5_000, priority_fee_lamports=100_000,
        notes="Swap w aplikacji portfela – zwykle doliczana jest opłata serwisowa ~0.85%.",
    ),
    # Giełda scentralizowana – zlecenia market (taker)
    "binance": FeeModel(
        name="binance", trading_fee_pct=0.001, slippage_pct=0.0001,
        base_fee_lamports=0, priority_fee_lamports=0, signatures=0,
        notes="CEX spot, taker 0.1% (0.075% przy płatności w BNB), spread ~0.01%.",
    ),
    "kraken": FeeModel(
        name="kraken", trading_fee_pct=0.004, slippage_pct=0.0002,
        base_fee_lamports=0, priority_fee_lamports=0, signatures=0,
        notes="CEX Kraken Pro, taker 0.40% przy niskim wolumenie.",
    ),
    "coinbase": FeeModel(
        name="coinbase", trading_fee_pct=0.006, slippage_pct=0.0002,
        base_fee_lamports=0, priority_fee_lamports=0, signatures=0,
        notes="CEX Coinbase Advanced, taker ~0.60% przy niskim wolumenie.",
    ),
    # Brak opłat – wyłącznie do porównań (NIE do realnej oceny strategii)
    "zero": FeeModel(
        name="zero", trading_fee_pct=0.0, slippage_pct=0.0, base_fee_lamports=0,
        priority_fee_lamports=0, signatures=0, notes="Bez opłat – tylko do porównania.",
    ),
}


def get_fee_model(name_or_dict) -> FeeModel:
    if isinstance(name_or_dict, FeeModel):
        return name_or_dict
    if isinstance(name_or_dict, dict):
        base = FEE_PROFILES.get(name_or_dict.get("name", ""), FeeModel())
        merged = {**base.to_dict(), **name_or_dict}
        return FeeModel.from_dict(merged)
    if name_or_dict not in FEE_PROFILES:
        raise ValueError(f"Nieznany profil opłat: {name_or_dict}. Dostępne: {', '.join(FEE_PROFILES)}")
    return FeeModel.from_dict(FEE_PROFILES[name_or_dict].to_dict())
