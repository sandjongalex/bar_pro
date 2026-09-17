# Routes Flask enregistrées

Générées depuis `app.url_map`. Cette liste prouve l’enregistrement, pas la validation de chaque comportement. Le contrat courant de l’API existante est [API.md](API.md) ; les tests exécutés sont détaillés dans [PROGRESS.md](PROGRESS.md).

| Méthodes | Route | Endpoint |
| --- | --- | --- |
| GET | `/` | `web.index` |
| POST | `/api/v1/auth/logout` | `api_auth.api_logout` |
| POST | `/api/v1/auth/tokens/refresh` | `api_auth.refresh` |
| POST | `/api/v1/auth/tokens` | `api_auth.token_login` |
| POST | `/api/v1/bars/<int:bar_id>/cash-handovers/<int:handover_id>/cancel` | `finance.cancel_handover` |
| POST | `/api/v1/bars/<int:bar_id>/cash-handovers/<int:handover_id>/post` | `finance.post_handover` |
| GET | `/api/v1/bars/<int:bar_id>/cash-handovers` | `finance.list_cash-handovers` |
| POST | `/api/v1/bars/<int:bar_id>/cash-handovers` | `finance.handover` |
| POST | `/api/v1/bars/<int:bar_id>/cash-movements/<int:movement_id>/reverse` | `finance.reverse` |
| GET | `/api/v1/bars/<int:bar_id>/cash-movements` | `finance.list_cash-movements` |
| POST | `/api/v1/bars/<int:bar_id>/cash-sessions/<int:session_id>/close` | `finance.close_session` |
| POST | `/api/v1/bars/<int:bar_id>/cash-sessions/<int:session_id>/movements` | `finance.movement` |
| GET | `/api/v1/bars/<int:bar_id>/cash-sessions/<int:session_id>` | `finance.session_detail` |
| GET | `/api/v1/bars/<int:bar_id>/cash-sessions` | `finance.list_cash-sessions` |
| POST | `/api/v1/bars/<int:bar_id>/cash-sessions` | `finance.open_session` |
| POST | `/api/v1/bars/<int:bar_id>/inventories/<int:inventory_id>/cancel` | `api_inventories.cancel` |
| PATCH | `/api/v1/bars/<int:bar_id>/inventories/<int:inventory_id>/counts` | `api_inventories.counts` |
| POST | `/api/v1/bars/<int:bar_id>/inventories/<int:inventory_id>/post` | `api_inventories.post` |
| GET | `/api/v1/bars/<int:bar_id>/inventories/<int:inventory_id>` | `api_inventories.api_detail` |
| GET | `/api/v1/bars/<int:bar_id>/inventories` | `api_inventories.api_list` |
| POST | `/api/v1/bars/<int:bar_id>/inventories` | `api_inventories.create` |
| GET | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/balance` | `finance.balance` |
| POST | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/cancel-with-refunds` | `finance.cancel_paid` |
| POST | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/cancel` | `orders.cancel` |
| POST | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/confirm` | `orders.confirm` |
| POST | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/returns` | `orders.returns` |
| POST | `/api/v1/bars/<int:bar_id>/orders/<int:order_id>/serve` | `orders.serve` |
| POST | `/api/v1/bars/<int:bar_id>/orders` | `orders.create` |
| GET | `/api/v1/bars/<int:bar_id>/payments` | `finance.list_payments` |
| POST | `/api/v1/bars/<int:bar_id>/payments` | `finance.payment` |
| GET | `/api/v1/bars/<int:bar_id>/products` | `api_catalog.api_list` |
| POST | `/api/v1/bars/<int:bar_id>/products` | `api_catalog.api_create` |
| GET | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>/balance` | `purchases.balance` |
| POST | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>/cancel` | `purchases.cancel` |
| POST | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>/payments` | `purchases.pay_purchase` |
| POST | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>/receive` | `purchases.receive` |
| GET | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>` | `purchases.get_purchase` |
| PATCH | `/api/v1/bars/<int:bar_id>/purchases/<int:purchase_id>` | `purchases.update_purchase` |
| GET | `/api/v1/bars/<int:bar_id>/purchases` | `purchases.list_purchases` |
| POST | `/api/v1/bars/<int:bar_id>/purchases` | `purchases.create` |
| GET | `/api/v1/bars/<int:bar_id>/refunds` | `finance.list_refunds` |
| POST | `/api/v1/bars/<int:bar_id>/refunds` | `finance.refund` |
| GET | `/api/v1/bars/<int:bar_id>/returns` | `finance.list_returns` |
| GET | `/api/v1/bars/<int:bar_id>/staff-cash/<int:staff_id>` | `finance.staff_balance` |
| GET | `/api/v1/bars/<int:bar_id>/staff-cash` | `finance.list_staff-cash` |
| POST | `/api/v1/bars/<int:bar_id>/staff` | `api_bars.staff` |
| GET | `/api/v1/bars/<int:bar_id>/stock/alerts` | `api_stock.alerts` |
| POST | `/api/v1/bars/<int:bar_id>/stock/movements` | `api_stock.move` |
| POST | `/api/v1/bars/<int:bar_id>/supplier-payments/<int:supplier_payment_id>/reverse` | `supplier_payments.reverse_supplier_payment` |
| GET | `/api/v1/bars/<int:bar_id>/supplier-payments` | `supplier_payments.list_supplier_payments` |
| GET | `/api/v1/bars/<int:bar_id>/suppliers/<int:supplier_id>` | `suppliers.get_supplier` |
| PATCH | `/api/v1/bars/<int:bar_id>/suppliers/<int:supplier_id>` | `suppliers.update_supplier` |
| GET | `/api/v1/bars/<int:bar_id>/suppliers` | `suppliers.list_suppliers` |
| POST | `/api/v1/bars/<int:bar_id>/suppliers` | `suppliers.create_supplier` |
| PATCH | `/api/v1/bars/<int:bar_id>` | `api_bars.patch` |
| GET | `/api/v1/bars` | `api_bars.list_bars` |
| POST | `/api/v1/bars` | `api_bars.create` |
| GET | `/api/v1/health` | `api.api_health` |
| GET | `/bars/<int:bar_id>/catalog` | `catalog.web_list` |
| GET, POST | `/bars/<int:bar_id>/finance` | `finance_web.desk` |
| GET | `/bars/<int:bar_id>/inventories/<int:inventory_id>/print` | `inventories.printable` |
| GET, POST | `/bars/<int:bar_id>/inventories/<int:inventory_id>` | `inventories.detail` |
| GET, POST | `/bars/<int:bar_id>/inventories/new` | `inventories.new` |
| GET | `/bars/<int:bar_id>/inventories` | `inventories.listing` |
| GET, POST | `/bars/<int:bar_id>/orders/new` | `orders_web.quick` |
| GET | `/bars/<int:bar_id>/stock` | `stock.history` |
| GET | `/bars/` | `bars.web_list` |
| GET | `/dashboard` | `web.dashboard` |
| GET | `/health` | `web.health` |
| GET, POST | `/login` | `auth.web_login` |
| POST | `/logout` | `auth.web_logout` |
| GET | `/static/<path:filename>` | `static` |
