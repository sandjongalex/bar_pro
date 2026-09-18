"""Default purchasing values used by the supplier purchase form.

Purchase prices are entry defaults only. They deliberately do not mutate
``Product.valuation_unit_cost`` because that field represents the cost of the
product's base stock unit (normally one bottle), not the supplier case price.

Case sizes are also defaults. Existing bars can use them immediately even when
``Product.units_per_case`` has not yet been populated. When a case purchase is
saved, the purchase service persists the selected case size on the product.
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

DEFAULT_UNITS_PER_CASE = {
    "33 export": 12,
    "mutzig": 12,
    "castel": 12,
    "dopel": 12,
    "isembeck": 12,
    "beaufort": 12,
    "castle": 12,
    "mayan": 12,
    "booster": 12,
    "chil": 12,
    "boster racine": 12,
    "heineken g": 12,
    "heineken": 24,
    "vampur": 24,
    "bavaria": 24,
    "vody": 24,
    "1664": 24,
    "p. guinness": 24,
    "g. guinness": 12,
    "origin": 12,
    "smooth gm": 15,
    "smooth pm": 24,
    "malta": 24,
    "harp": 12,
    "ice g": 12,
    "ice": 24,
    "kadji": 12,
    "k44": 12,
    "booster gin g": 12,
    "tonic": 12,
    "malta tonic": 24,
    "soda": 12,
    "top 12": 12,
    "coca 12": 12,
    "vinto 12": 12,
    "coca pet": 6,
    "top pet": 6,
    "djino pet": 6,
    "kq": 12,
    "ucb": 12,
    "spécial pamplemousse": 12,
    "vimto": 6,
    "ucb pet": 6,
    "reactor": 12,
    "sprite": 6,
    "délice": 24,
    "orangina": 6,
    "eau super m": 6,
    "opur": 6,
    "vital": 6,
    "cuve du roi": 12,
    "el vino": 12,
    "c. morgan": 24,
    "japap": 6,
    "black and": 12,
}


def _product_key(product_name: str) -> str:
    return (product_name or "").strip().casefold()


def default_purchase_price(product_name: str, currency: str, fallback) -> Decimal:
    """Return the configured purchase price default for CFA-denominated bars."""
    fallback_value = Decimal(str(fallback or 0))
    if (currency or "").upper() not in CFA_CURRENCIES:
        return fallback_value
    return DEFAULT_PURCHASE_PRICES_CFA.get(_product_key(product_name), fallback_value)


def default_units_per_case(product_name: str, fallback=None):
    """Return the configured bottles-per-case default for a known product.

    A positive explicit product value always wins, so bar-specific configuration
    remains authoritative. Unknown products keep their supplied fallback.
    """
    if fallback not in (None, ""):
        try:
            parsed = int(fallback)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return DEFAULT_UNITS_PER_CASE.get(_product_key(product_name))
