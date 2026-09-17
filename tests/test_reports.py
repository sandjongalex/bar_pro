from decimal import Decimal
import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance
from app.order_services import order_service
from app.report_services import summary, consolidated
from app.stock_service import stock_service
from test_workflows import env, order


def test_report_low_stock_is_exact_and_excludes_archived_and_foreign(env):
    _, owner, bar, foreign, product, _, _, _ = env
    bar.stock_alert_threshold = Decimal("2")
    category = db.session.get(ProductCategory, product.category_id)
    equal = Product(bar_id=bar.id, category_id=category.id, name="Equal", sku="EQUAL", base_unit="bottle", sale_price=10, valuation_unit_cost=4)
    below = Product(bar_id=bar.id, category_id=category.id, name="Below", sku="BELOW", base_unit="bottle", sale_price=10, valuation_unit_cost=4)
    archived = Product(bar_id=bar.id, category_id=category.id, name="Archived", sku="ARCH", base_unit="bottle", sale_price=10, valuation_unit_cost=4, is_active=False)
    foreign_category = ProductCategory(bar_id=foreign.id, name="Foreign")
    foreign_product = Product(bar_id=foreign.id, category_id=foreign_category.id, name="Foreign", sku="FOREIGN", base_unit="bottle", sale_price=10, valuation_unit_cost=4)
    db.session.add(foreign_category); db.session.flush()
    foreign_product.category_id=foreign_category.id
    db.session.add_all([equal, below, archived, foreign_product]); db.session.flush()
    for item, quantity in ((equal, 2), (below, 1)):
        stock_service.move(owner, item.bar_id, item.id, "INITIAL", quantity, "Opening")
    db.session.add_all([StockBalance(bar_id=bar.id, product_id=archived.id, quantity=0, version=0), StockBalance(bar_id=foreign.id, product_id=foreign_product.id, quantity=0, version=0)])
    db.session.commit()

    low = summary(owner, bar.id)["stock"]["low"]
    assert {row["product"] for row in low} == {"Equal", "Below"}
    assert {row["difference"] for row in low} == {"0.000000", "-1.000000"}
    assert all(row["category"] == "Drinks" for row in low)


def test_report_losses_uses_only_loss_movements(env):
    _, owner, bar, _, product, _, _, _ = env
    stock_service.move(owner, bar.id, product.id, "LOSS", -2, "Broken bottles")
    db.session.commit()
    report = summary(owner, bar.id)
    assert Decimal(report["stock"]["losses"]) == Decimal("2")


def test_report_math_sales_margin_payments_and_receivable(env):
    _, owner, bar, _, product, _, _, _ = env
    value = order_service.create(owner, bar.id, "REPORT-ORDER", [{"product_id": product.id, "quantity": 2}])
    order_service.confirm(owner, bar.id, value.id)
    db.session.commit()
    report = summary(owner, bar.id)
    assert report["sales"]["revenue"] == "200.0000"
    assert Decimal(report["sales"]["gross_margin_estimate"]) == Decimal("120")
    assert report["receivables"]["total_due"] == "200.0000"


def test_consolidation_rebuilds_owner_scope_and_excludes_other_owner(env):
    _, owner, bar, foreign, product, _, _, _ = env
    second = Bar(owner_id=owner.id, name="A2", timezone=bar.timezone, currency="XAF")
    other_category = ProductCategory(bar_id=foreign.id, name="B1")
    second_category = ProductCategory(bar_id=second.id, name="A2")
    db.session.add(second); db.session.flush()
    second_category.bar_id=second.id
    db.session.add_all([second_category, other_category]); db.session.flush()
    second_product = Product(bar_id=second.id, category_id=second_category.id, name="A2 Water", sku="A2-WATER", base_unit="bottle", sale_price=50, valuation_unit_cost=20)
    db.session.add(second_product); db.session.flush()
    stock_service.move(owner, second.id, second_product.id, "INITIAL", 5, "Opening")
    a_order = order_service.create(owner, second.id, "A2-ORDER", [{"product_id": second_product.id, "quantity": 2}])
    order_service.confirm(owner, second.id, a_order.id)
    db.session.commit()

    result = consolidated(owner)
    ids = {item["bar_id"] for item in result["bars"]}
    assert ids == {bar.id, second.id}
    assert foreign.id not in ids
    assert sum(Decimal(item["report"]["sales"]["revenue"]) for item in result["bars"]) == Decimal("100.0000")
    with pytest.raises(PermissionError):
        summary(owner, foreign.id)

def test_report_csv_and_print_use_same_values_and_permissions(env):
    app, owner, bar, foreign, product, _, headers, _ = env
    value=order_service.create(owner,bar.id,"CSV-ORDER",[{"product_id":product.id,"quantity":1}]);order_service.confirm(owner,bar.id,value.id);db.session.commit()
    client=app.test_client(); response=client.get(f"/api/v1/bars/{bar.id}/reports/csv",headers=headers)
    assert response.status_code==200; assert "text/csv" in response.content_type; assert "revenue" in response.data.decode("utf-8-sig"); assert "100.0000" in response.data.decode("utf-8-sig")
    printed=client.get(f"/api/v1/bars/{bar.id}/reports/print",headers=headers)
    assert printed.status_code==200 and "Rapport opérationnel" in printed.text and "100.0000" in printed.text
    assert client.get(f"/api/v1/bars/{foreign.id}/reports/csv",headers=headers).status_code in {403,404}
