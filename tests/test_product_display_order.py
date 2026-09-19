from sqlalchemy import select

from app.extensions import db
from app.models import Product
from app.product_display_order import PRODUCT_DISPLAY_ORDER, product_display_rank, product_order_expression
from test_workflows import env


def test_requested_product_sequence_is_stable():
    assert PRODUCT_DISPLAY_ORDER == (
        "33 Export", "Mutzig", "Castel", "Dopel", "Isembeck", "Beaufort", "Castle", "Mayan",
        "Booster", "Chil", "Boster Racine", "Tonic", "Malta Tonic", "Soda", "Top 12", "Coca 12",
        "Vinto 12", "Coca PET", "Top PET", "Djino PET", "Heineken G", "Heineken", "Vampur", "Bavaria",
        "Vody", "1664", "Perlado", "El Vino", "C. Morgan", "P. Guinness", "G. Guinness", "Origin",
        "Smooth GM", "Smooth PM", "Malta", "Harp", "Ice G", "Ice", "KQ", "Kadji", "K44", "UCB",
        "Eau Super M", "Opur", "Vital", "Spécial Pamplemousse", "Japap", "Vimto", "UCB PET",
        "Black And", "Reactor", "Booster Gin G", "Sprite", "Délice", "Orangina",
    )


def test_common_name_variants_keep_requested_rank():
    assert product_display_rank("33") == product_display_rank("33 Export")
    assert product_display_rank("c.morgan") == product_display_rank("C. Morgan")
    assert product_display_rank("p.guinness") == product_display_rank("P. Guinness")
    assert product_display_rank("g.guinness") == product_display_rank("G. Guinness")
    assert product_display_rank("special pamplemous") == product_display_rank("Spécial Pamplemousse")
    assert product_display_rank("booster gin") == product_display_rank("Booster Gin G")
    assert product_display_rank("delice") == product_display_rank("Délice")


def test_sql_product_order_uses_business_sequence(env):
    _, _, bar, _, seed_product, _, _, _ = env
    category_id = seed_product.category_id
    names = ["Orangina", "Heineken", "ZZ Custom", "33 Export", "Perlado", "Heineken G", "Tonic"]
    for index, name in enumerate(names, 1):
        db.session.add(
            Product(
                bar_id=bar.id,
                category_id=category_id,
                sku=f"ORDER-{index}",
                name=name,
                base_unit="bouteille",
                sale_price=100,
                valuation_unit_cost=50,
                stock_alert_threshold=0,
                is_active=True,
            )
        )
    db.session.commit()

    rows = list(
        db.session.scalars(
            select(Product)
            .where(Product.bar_id == bar.id, Product.name.in_(names))
            .order_by(product_order_expression(Product.name), Product.name, Product.id)
        )
    )
    assert [row.name for row in rows] == [
        "33 Export",
        "Tonic",
        "Heineken G",
        "Heineken",
        "Perlado",
        "Orangina",
        "ZZ Custom",
    ]
