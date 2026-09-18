from decimal import Decimal

from app.purchase_defaults import DEFAULT_PURCHASE_PRICES_CFA, default_purchase_price


EXPECTED_PURCHASE_PRICES = {
    "33 export": "7800",
    "mutzig": "7800",
    "castel": "7800",
    "dopel": "7800",
    "isembeck": "8900",
    "beaufort": "7800",
    "castle": "7800",
    "mayan": "7200",
    "booster": "7800",
    "chil": "7200",
    "boster racine": "7800",
    "heineken g": "10500",
    "heineken": "20000",
    "vampur": "15000",
    "bavaria": "16000",
    "vody": "15500",
    "1664": "20000",
    "p. guinness": "16100",
    "g. guinness": "11200",
    "origin": "7800",
    "smooth gm": "10500",
    "smooth pm": "0",
    "malta": "13000",
    "harp": "7800",
    "ice g": "10500",
    "ice": "14000",
    "kadji": "7800",
    "k44": "7800",
    "booster gin g": "10500",
    "tonic": "2500",
    "malta tonic": "2500",
    "soda": "3000",
    "top 12": "3500",
    "coca 12": "3500",
    "vinto 12": "4100",
    "coca pet": "3500",
    "top pet": "3500",
    "djino pet": "3500",
    "kq": "2500",
    "ucb": "4100",
    "spécial pamplemousse": "5400",
    "vimto": "4000",
    "ucb pet": "3500",
    "reactor": "4000",
    "sprite": "4000",
    "délice": "13000",
    "orangina": "4000",
    "eau super m": "1500",
    "opur": "1300",
    "vital": "1200",
    "cuve du roi": "12000",
    "el vino": "12000",
    "c. morgan": "15000",
    "japap": "15000",
    "black and": "6000",
}


def test_default_purchase_prices_match_supplied_catalog():
    assert DEFAULT_PURCHASE_PRICES_CFA == {
        name: Decimal(value) for name, value in EXPECTED_PURCHASE_PRICES.items()
    }


def test_default_purchase_price_is_case_insensitive_for_cfa_bars():
    assert default_purchase_price("  33 Export  ", "XAF", Decimal("123")) == Decimal("7800")
    assert default_purchase_price("SPÉCIAL PAMPLEMOUSSE", "XOF", Decimal("123")) == Decimal("5400")


def test_default_purchase_price_falls_back_for_unknown_product():
    assert default_purchase_price("Produit libre", "XAF", Decimal("975")) == Decimal("975")


def test_default_purchase_price_does_not_apply_cfa_amounts_to_other_currencies():
    assert default_purchase_price("33 Export", "EUR", Decimal("2.50")) == Decimal("2.50")
