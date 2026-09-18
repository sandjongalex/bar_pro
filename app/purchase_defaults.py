"""Default supplier purchase prices used by the purchasing form.

These values are purchase-entry defaults only. They deliberately do not mutate
``Product.valuation_unit_cost`` because that field is used as the cost snapshot
for sales and therefore follows the product's base stock unit rather than a
supplier case price.
"""
from __future__ import annotations

from decimal import Decimal


CFA_CURRENCIES = {"XAF", "XOF"}

DEFAULT_PURCHASE_PRICES_CFA = {
    "33 export": Decimal("7800"),
    "mutzig": Decimal("7800"),
    "castel": Decimal("7800"),
    "dopel": Decimal("7800"),
    "isembeck": Decimal("8900"),
    "beaufort": Decimal("7800"),
    "castle": Decimal("7800"),
    "mayan": Decimal("7200"),
    "booster": Decimal("7800"),
    "chil": Decimal("7200"),
    "boster racine": Decimal("7800"),
    "heineken g": Decimal("10500"),
    "heineken": Decimal("20000"),
    "vampur": Decimal("15000"),
    "bavaria": Decimal("16000"),
    "vody": Decimal("15500"),
    "1664": Decimal("20000"),
    "p. guinness": Decimal("16100"),
    "g. guinness": Decimal("11200"),
    "origin": Decimal("7800"),
    "smooth gm": Decimal("10500"),
    "smooth pm": Decimal("0"),
    "malta": Decimal("13000"),
    "harp": Decimal("7800"),
    "ice g": Decimal("10500"),
    "ice": Decimal("14000"),
    "kadji": Decimal("7800"),
    "k44": Decimal("7800"),
    "booster gin g": Decimal("10500"),
    "tonic": Decimal("2500"),
    "malta tonic": Decimal("2500"),
    "soda": Decimal("3000"),
    "top 12": Decimal("3500"),
    "coca 12": Decimal("3500"),
    "vinto 12": Decimal("4100"),
    "coca pet": Decimal("3500"),
    "top pet": Decimal("3500"),
    "djino pet": Decimal("3500"),
    "kq": Decimal("2500"),
    "ucb": Decimal("4100"),
    "spécial pamplemousse": Decimal("5400"),
    "vimto": Decimal("4000"),
    "ucb pet": Decimal("3500"),
    "reactor": Decimal("4000"),
    "sprite": Decimal("4000"),
    "délice": Decimal("13000"),
    "orangina": Decimal("4000"),
    "eau super m": Decimal("1500"),
    "opur": Decimal("1300"),
    "vital": Decimal("1200"),
    "cuve du roi": Decimal("12000"),
    "el vino": Decimal("12000"),
    "c. morgan": Decimal("15000"),
    "japap": Decimal("15000"),
    "black and": Decimal("6000"),
}


def default_purchase_price(product_name: str, currency: str, fallback) -> Decimal:
    """Return the configured purchase default without changing product data.

    The supplied catalogue is CFA-denominated. Bars using another currency keep
    their product valuation cost as the form fallback rather than treating CFA
    amounts as another currency.
    """
    fallback_value = Decimal(str(fallback or 0))
    if (currency or "").upper() not in CFA_CURRENCIES:
        return fallback_value
    return DEFAULT_PURCHASE_PRICES_CFA.get((product_name or "").strip().casefold(), fallback_value)
