"""Warehouse-specific roles, delegation targets and human-readable labels.

These are *new* permissions, not aliases for cashier/supplier/finance rights.
"""
WAREHOUSE_DELEGABLE = {
    "warehouse.main.read": "Consulter le stock principal",
    "warehouse.cashier.needs.read": "Voir les besoins de la caisse",
    "warehouse.requests.read": "Consulter les demandes",
    "warehouse.notifications.read": "Recevoir les notifications de demandes",
    "warehouse.requests.prepare": "Préparer les approvisionnements",
    "warehouse.transfers.dispatch": "Expédier vers la caisse",
    "warehouse.transfers.read": "Consulter les transferts",
    "warehouse.main.inventory.count": "Compter le stock principal",
    "warehouse.discrepancies.read": "Consulter les écarts de transferts",
    "warehouse.requests.create": "Demander un approvisionnement",
    "warehouse.transfers.receive": "Recevoir un transfert à la caisse",
    "warehouse.purchases.manage": "Autorisation spéciale d'achats fournisseurs",
    "warehouse.purchases.receive": "Confirmer la réception physique des achats",
}

# Only these actions are automatically available to the STOREKEEPER;
# purchasing requires a separate, owner-granted capability.
STOREKEEPER_DEFAULT = frozenset(
    {
        "warehouse.main.read",
        "warehouse.cashier.needs.read",
        "warehouse.requests.read",
        "warehouse.notifications.read",
        "warehouse.requests.prepare",
        "warehouse.transfers.dispatch",
        "warehouse.transfers.read",
        "warehouse.main.inventory.count",
        "warehouse.discrepancies.read",
    }
)

WAREHOUSE_WRITES = frozenset({
    "warehouse.requests.create",
    "warehouse.requests.prepare",
    "warehouse.transfers.dispatch",
    "warehouse.transfers.receive",
    "warehouse.main.inventory.count",
    "warehouse.discrepancies.resolve",
    "warehouse.purchases.manage",
    "warehouse.purchases.receive",
    "warehouse.permissions.manage",
})
