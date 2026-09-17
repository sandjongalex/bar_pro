"""Physical counts preserve snapshots; only StockService changes stock."""
from sqlalchemy import select
from app.extensions import db
from app.models import Inventory, InventoryLine, Product, StockBalance, utcnow
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


def product_identifier(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError("INVALID_PRODUCT_ID")
    result = int(value)
    if not 0 < result < 2**64:
        raise ValueError("INVALID_PRODUCT_ID")
    return result


class InventoryService:
    def get(self, actor, bar_id, inventory_id, *, write=False):
        permissions.require(actor, "inventory.adjust" if write else "inventory.read", bar_id)
        query = select(Inventory).where(Inventory.id == inventory_id, Inventory.bar_id == bar_id)
        if write:
            query = query.with_for_update().execution_options(populate_existing=True)
        inv = db.session.scalar(query)
        if inv is None:
            raise LookupError("NOT_FOUND")
        if write and inv.status != "DRAFT":
            raise ValueError("INVENTORY_IMMUTABLE")
        return inv

    def create(self, actor, bar_id, reference, product_ids, reason):
        permissions.require(actor, "inventory.adjust", bar_id)
        reference = required_text(reference, 64)
        reason = required_text(reason)
        if not isinstance(product_ids, list) or not product_ids:
            raise ValueError("INVALID_INVENTORY")
        ids = [product_identifier(value) for value in product_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("DUPLICATE_PRODUCT")
        products = list(db.session.scalars(select(Product).where(Product.id.in_(ids), Product.bar_id == bar_id).order_by(Product.id).with_for_update()))
        if len(products) != len(ids):
            raise LookupError("NOT_FOUND")
        inv = Inventory(bar_id=bar_id, reference=reference, status="DRAFT", counted_at=utcnow(), created_by_id=actor.id, reason=reason)
        db.session.add(inv)
        db.session.flush()
        for product in products:
            balance = db.session.scalar(select(StockBalance).where(StockBalance.product_id == product.id, StockBalance.bar_id == bar_id).with_for_update())
            if balance is None:
                balance = StockBalance(bar_id=bar_id, product_id=product.id, quantity=0, version=0)
                db.session.add(balance)
                db.session.flush()
            db.session.add(InventoryLine(bar_id=bar_id, inventory_id=inv.id, product_id=product.id, expected_quantity_snapshot=balance.quantity, balance_version_snapshot=balance.version))
        return inv

    def count(self, actor, bar_id, inventory_id, quantities):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("COUNT_REQUIRED")
        lines = {line.product_id: line for line in db.session.scalars(select(InventoryLine).where(InventoryLine.inventory_id == inv.id).with_for_update())}
        values = {}
        for key, raw in quantities.items():
            product_id = product_identifier(key)
            if product_id not in lines or product_id in values:
                raise ValueError("INVALID_COUNT_PRODUCT")
            value = number(raw, 6)
            if value < 0:
                raise ValueError("INVALID_COUNT")
            values[product_id] = value
        for product_id, value in values.items():
            lines[product_id].counted_quantity = value
        inv.counted_at = utcnow()
        return inv

    def post(self, actor, bar_id, inventory_id):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        lines = list(db.session.scalars(select(InventoryLine).where(InventoryLine.inventory_id == inv.id).order_by(InventoryLine.product_id).with_for_update()))
        if not lines:
            raise ValueError("COUNT_REQUIRED")
        for line in lines:
            if line.counted_quantity is None:
                raise ValueError("COUNT_REQUIRED")
            balance = db.session.scalar(select(StockBalance).where(StockBalance.bar_id == bar_id, StockBalance.product_id == line.product_id).with_for_update())
            if balance is None or balance.version != line.balance_version_snapshot:
                raise ValueError("INVENTORY_STALE")
        for line in lines:
            delta = line.counted_quantity - line.expected_quantity_snapshot
            if delta:
                stock_service.move(actor, bar_id, line.product_id, "INVENTORY_ADJUSTMENT", delta, f"Inventaire {inv.reference}", inventory_line_id=line.id)
        inv.status = "POSTED"
        inv.posted_at = utcnow()
        return inv

    def cancel(self, actor, bar_id, inventory_id):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        inv.status = "CANCELLED"
        inv.cancelled_at = utcnow()
        return inv


inventory_service = InventoryService()
