"""Read-only, tenant-scoped operational reporting projections."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import (Bar, Customer, Order, OrderLine, Payment, Refund, Product, StockBalance,
                        StockMovement, Expense, CashSession, Purchase, SupplierPayment,
                        Inventory, InventoryLine, ProductCategory)
from app.permissions import permissions


def _date(value, end=False):
    if not value: return None
    try: return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if "T" in value else datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError: raise ValueError("INVALID_DATE")


def summary(actor, bar_id, start=None, end=None):
    permissions.require(actor, "reports.read", bar_id)
    start, end = _date(start), _date(end, True)
    def in_period(column):
        return [column >= start] if start and not end else [column < end] if end and not start else [column >= start, column < end] if start and end else []

    # Orders waiting for the cashier are not sales. Revenue is recognized in this
    # operational report only after the order is delivered and fully settled
    # (real payment or customer credit).
    all_orders=list(db.session.scalars(select(Order).where(Order.bar_id==bar_id)))
    sales=[
        o for o in all_orders
        if o.status in {"CONFIRMED","SERVED"}
        and o.payment_status=="PAID"
        and (not start or (o.posted_at and o.posted_at>=start))
        and (not end or (o.posted_at and o.posted_at<end))
    ]
    sale_ids={o.id for o in sales}
    sales_lines=list(db.session.scalars(select(OrderLine).where(OrderLine.bar_id==bar_id, OrderLine.order_id.in_(sale_ids)))) if sale_ids else []

    delivered_unsettled=[
        o for o in all_orders
        if o.status in {"CONFIRMED","SERVED"}
        and o.payment_status in {"UNPAID","PARTIAL"}
        and (not start or (o.posted_at and o.posted_at>=start))
        and (not end or (o.posted_at and o.posted_at<end))
    ]

    payments=list(db.session.scalars(select(Payment).where(Payment.bar_id==bar_id, *in_period(Payment.received_at))))
    refunds=list(db.session.scalars(select(Refund).where(Refund.bar_id==bar_id, *in_period(Refund.refunded_at))))
    expenses=list(db.session.scalars(select(Expense).where(Expense.bar_id==bar_id, *in_period(Expense.incurred_at))))
    revenue=sum((x.total_amount for x in sales_lines),Decimal(0))
    margin=sum((x.quantity*(x.unit_sale_price_snapshot-x.unit_cost_snapshot) for x in sales_lines),Decimal(0))
    by_method=defaultdict(Decimal)
    for p in payments: by_method[p.method]+=p.amount_applied
    top=defaultdict(Decimal)
    for x in sales_lines: top[x.product_name_snapshot]+=x.quantity

    from app.finance_totals import order_balance
    unpaid=[]
    for o in delivered_unsettled:
        b=order_balance(o)
        if b["amount_due"]>0:
            unpaid.append({"order_id":o.id,"amount_due":str(b["amount_due"])})

    customers={c.id:c for c in db.session.scalars(select(Customer).where(Customer.bar_id==bar_id))}
    customer_debts=defaultdict(Decimal)
    for entry in db.session.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.bar_id==bar_id)):
        customer_debts[entry.customer_id]+=entry.amount_delta
    customer_accounts=[
        {"customer_id":customer_id,"customer":customers[customer_id].display_name if customer_id in customers else str(customer_id),"amount_due":str(max(amount,Decimal(0)))}
        for customer_id,amount in customer_debts.items() if amount>0
    ]
    customer_credit_total=sum((Decimal(item["amount_due"]) for item in customer_accounts),Decimal(0))

    balances=list(db.session.scalars(select(StockBalance).where(StockBalance.bar_id==bar_id)))
    bar=db.session.get(Bar,bar_id)
    products={p.id:p for p in db.session.scalars(select(Product).where(Product.bar_id==bar_id))}
    categories={c.id:c.name for c in db.session.scalars(select(ProductCategory).where(ProductCategory.bar_id==bar_id))}
    low=[{"product_id":b.product_id,"product":products[b.product_id].name,"category":categories.get(products[b.product_id].category_id),"quantity":str(b.quantity),"threshold":str(bar.stock_alert_threshold),"difference":str(b.quantity-bar.stock_alert_threshold)} for b in balances if b.quantity<=bar.stock_alert_threshold and b.product_id in products and products[b.product_id].is_active]
    losses=sum((-m.quantity_delta for m in db.session.scalars(select(StockMovement).where(StockMovement.bar_id==bar_id,StockMovement.movement_type=="LOSS",*in_period(StockMovement.occurred_at))) if m.quantity_delta<0),Decimal(0))
    cash=[str(s.closing_difference) for s in db.session.scalars(select(CashSession).where(CashSession.bar_id==bar_id,CashSession.closing_difference.is_not(None),*in_period(CashSession.closed_at)))]
    inv=[str(x.difference_quantity) for x in db.session.scalars(select(InventoryLine).join(Inventory).where(Inventory.bar_id==bar_id,Inventory.status=="POSTED",*in_period(Inventory.posted_at)))]
    purchases=list(db.session.scalars(select(Purchase).where(Purchase.bar_id==bar_id,Purchase.status!="CANCELLED")))
    debt=sum((p.total_amount for p in purchases),Decimal(0))
    supplier_payments=list(db.session.scalars(select(SupplierPayment).where(SupplierPayment.bar_id==bar_id)))
    debt-=sum((x.amount if x.entry_kind=="PAYMENT" else -x.amount for x in supplier_payments),Decimal(0))
    return {
        "period":{"start":start.isoformat() if start else None,"end":end.isoformat() if end else None},
        "sales":{"revenue":str(revenue),"orders":len(sales),"gross_margin_estimate":str(margin),"margin_note":"Ventes livrées et soldées uniquement. Indicateur de gestion basé sur les snapshots historiques, pas une comptabilité légale."},
        "payments":{"received":str(sum((p.amount_applied for p in payments),Decimal(0))),"refunded":str(sum((r.amount for r in refunds),Decimal(0))),"by_method":{k:str(v) for k,v in by_method.items()}},
        "receivables":{"orders_unpaid":unpaid,"orders_total_due":str(sum((order_balance(o)["amount_due"] for o in delivered_unsettled),Decimal(0))),"customer_credit_total":str(customer_credit_total),"customer_accounts":customer_accounts},
        "top_products":[{"name":k,"quantity":str(v)} for k,v in sorted(top.items(),key=lambda item:item[1],reverse=True)],
        "expenses":{"total":str(sum((e.amount for e in expenses if e.entry_kind=="EXPENSE"),Decimal(0)))},
        "stock":{"low":low,"losses":str(losses)},
        "cash":{"closing_differences":cash},
        "inventories":{"differences":inv},
        "supplier_payables":{"total_due":str(max(debt,Decimal(0)))}
    }


def consolidated(actor, start=None, end=None):
    if actor.category!="OWNER": raise PermissionError("FORBIDDEN")
    bars=list(db.session.scalars(select(Bar).where(Bar.owner_id==actor.id)))
    reports=[summary(actor,b.id,start,end) for b in bars]
    return {"bars":[{"bar_id":b.id,"name":b.name,"report":r} for b,r in zip(bars,reports)],"bar_count":len(bars)}
