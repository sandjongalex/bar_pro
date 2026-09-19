"""Canonical product display order used by operational product lists.

Known Kape Bar products follow the explicit business order. Products that are
not part of the list are kept afterwards and can then be ordered by name/id by
the caller.
"""
from __future__ import annotations

from sqlalchemy import case, func


PRODUCT_DISPLAY_ORDER = (
    "33 Export",
    "Mutzig",
    "Castel",
    "Dopel",
    "Isembeck",
    "Beaufort",
    "Castle",
    "Mayan",
    "Booster",
    "Chil",
    "Boster Racine",
    "Tonic",
    "Malta Tonic",
    "Soda",
    "Top 12",
    "Coca 12",
    "Vinto 12",
    "Coca PET",
    "Top PET",
    "Djino PET",
    "Heineken G",
    "Heineken",
    "Vampur",
    "Bavaria",
    "Vody",
    "1664",
    "Perlado",
    "El Vino",
    "C. Morgan",
    "P. Guinness",
    "G. Guinness",
    "Origin",
    "Smooth GM",
    "Smooth PM",
    "Malta",
    "Harp",
    "Ice G",
    "Ice",
    "KQ",
    "Kadji",
    "K44",
    "UCB",
    "Eau Super M",
    "Opur",
    "Vital",
    "Spécial Pamplemousse",
    "Japap",
    "Vimto",
    "UCB PET",
    "Black And",
    "Reactor",
    "Booster Gin G",
    "Sprite",
    "Délice",
    "Orangina",
)


_PRODUCT_RANK = {name.casefold(): index for index, name in enumerate(PRODUCT_DISPLAY_ORDER)}
_PRODUCT_RANK.update(
    {
        "33": _PRODUCT_RANK["33 export"],
        "c.morgan": _PRODUCT_RANK["c. morgan"],
        "p.guinness": _PRODUCT_RANK["p. guinness"],
        "g.guinness": _PRODUCT_RANK["g. guinness"],
        "special pamplemous": _PRODUCT_RANK["spécial pamplemousse"],
        "special pamplemousse": _PRODUCT_RANK["spécial pamplemousse"],
        "booster gin": _PRODUCT_RANK["booster gin g"],
        "delice": _PRODUCT_RANK["délice"],
    }
)


def product_display_rank(name: str | None) -> int:
    """Return the requested display rank, with unknown products last."""
    normalized = str(name or "").strip().casefold()
    return _PRODUCT_RANK.get(normalized, len(PRODUCT_DISPLAY_ORDER))


def product_order_expression(column):
    """SQLAlchemy expression matching :func:`product_display_rank` for DB lists."""
    sql_ranks = {name.lower(): rank for name, rank in _PRODUCT_RANK.items()}
    return case(
        sql_ranks,
        value=func.lower(func.trim(column)),
        else_=len(PRODUCT_DISPLAY_ORDER),
    )
