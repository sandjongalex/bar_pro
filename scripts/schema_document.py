"""Generate the implemented schema dictionary. Run from repository root."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import configure_mappers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extensions import db
from app import models  # noqa: F401  # import registers SQLAlchemy models


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "versions"


def _markdown(value) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _column_list(columns) -> str:
    return ", ".join(column.name for column in columns) or "—"


def _target_list(constraint: ForeignKeyConstraint) -> str:
    return ", ".join(element.target_fullname for element in constraint.elements)


def _default(default) -> str:
    if default is None:
        return "—"
    arg = default.arg
    if callable(arg):
        return getattr(arg, "__name__", repr(arg))
    return repr(arg)


def _server_default(column) -> str:
    if column.server_default is None:
        return "—"
    if column.computed is not None:
        return "—"
    return str(getattr(column.server_default, "arg", column.server_default))


def _computed(column) -> str:
    if column.computed is None:
        return "—"
    return str(column.computed.sqltext)


def _type(column) -> str:
    mysql_type = column.type.compile(dialect=mysql.dialect())
    sqlite_type = column.type.compile(dialect=sqlite.dialect())
    if mysql_type == sqlite_type:
        return mysql_type
    return f"MySQL `{mysql_type}` / SQLite `{sqlite_type}`"


def _migration_records():
    records = []
    for path in sorted(MIGRATIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r"\brevision\s*=\s*['\"]([^'\"]+)['\"]", text)
        down_line = re.search(r"\bdown_revision\s*=\s*([^;\n]+)", text)
        down_revisions: list[str] = []
        if down_line:
            raw = down_line.group(1).split(";", 1)[0].strip()
            if raw != "None":
                down_revisions = re.findall(r"['\"]([^'\"]+)['\"]", raw)
        try:
            title = ast.get_docstring(ast.parse(text)) or path.stem
        except SyntaxError:
            title = path.stem
        if revision:
            records.append(
                {
                    "revision": revision.group(1),
                    "down": down_revisions,
                    "file": path.name,
                    "title": title,
                }
            )
    referenced = {down for record in records for down in record["down"]}
    heads = sorted(record["revision"] for record in records if record["revision"] not in referenced)
    return records, heads


def _constraint_sort_key(constraint):
    return (
        constraint.__class__.__name__,
        constraint.name or "",
        tuple(column.name for column in getattr(constraint, "columns", [])),
    )


def _foreign_key_edges():
    edges = []
    for table in sorted(db.metadata.tables.values(), key=lambda item: item.name):
        for constraint in sorted(table.constraints, key=_constraint_sort_key):
            if isinstance(constraint, ForeignKeyConstraint):
                targets = [element.column.table.name for element in constraint.elements]
                target_table = targets[0] if targets else "?"
                edges.append(
                    (
                        target_table,
                        table.name,
                        ", ".join(column.name for column in constraint.columns),
                        _target_list(constraint),
                        constraint.name or "—",
                        constraint.ondelete or "—",
                    )
                )
    return edges


def _orm_relationships():
    configure_mappers()
    rows = []
    for mapper in sorted(db.Model.registry.mappers, key=lambda item: item.local_table.name):
        for relationship in sorted(mapper.relationships, key=lambda item: item.key):
            rows.append(
                (
                    mapper.local_table.name,
                    relationship.key,
                    relationship.mapper.local_table.name,
                    relationship.direction.name,
                    "liste" if relationship.uselist else "scalaire",
                )
            )
    return rows


def _header() -> str:
    records, heads = _migration_records()
    head_text = ", ".join(f"`{head}`" for head in heads) if heads else "À déterminer"
    migration_rows = [
        "| Révision | Depuis | Fichier | Objet déclaré |",
        "| --- | --- | --- | --- |",
    ]
    for record in records:
        down = ", ".join(f"`{revision}`" for revision in record["down"]) or "base"
        migration_rows.append(
            f"| `{record['revision']}` | {down} | `{record['file']}` | {_markdown(record['title'])} |"
        )

    return f"""# Modèle de données physique implémenté

Ce fichier est généré par [`scripts/schema_document.py`](../scripts/schema_document.py).
Ne pas l'éditer librement : [`tests/test_schema.py`](../tests/test_schema.py) compare exactement
son contenu à `scripts.schema_document.render()`.

Révision Alembic terminale observée : {head_text}. Le dictionnaire décrit les tables,
colonnes, clés, contraintes, index et relations dérivables de `app.models`.
Les types sont compilés pour MySQL et SQLite quand ils diffèrent. Une compilation SQL ou
un test SQLite ne constitue pas une validation sur serveur MySQL.

## Portée et limites

- Le schéma physique est centralisé dans [`app/models.py`](../app/models.py) et migré par
  [`migrations/versions/`](../migrations/versions/).
- Les identifiants utilisent `ID = BigInteger` avec variante `Integer` SQLite.
- Les montants utilisent `MONEY = Numeric(19, 4)` ; les quantités utilisent `QTY = Numeric(20, 6)`.
- Les ressources rattachées à un bar portent `bar_id` et des FK composites quand la cible est
  elle-même isolée par bar. Cette portée SQL ne remplace pas les contrôles de permissions.
- Les defaults listés sont distingués entre defaults ORM Python, defaults serveur et colonnes calculées.
  Un `INSERT` SQL brut doit donc renseigner les colonnes obligatoires sans default serveur.
- Les contraintes SQL listées sont les protections réellement déclarées : PK, FK, UNIQUE, CHECK et INDEX.
  Les validations de service, les permissions, les transitions d'état et les règles inter-lignes
  ne sont pas transformées en contraintes SQL dans ce document.
- L'existence d'une table ne prouve pas qu'un module métier soit livré. Par exemple,
  `user_sessions` et `idempotency_records` documentent un stockage possible, pas un workflow complet.
- Aucun trigger, cascade implicite, immutabilité SQL ou garantie de concurrence n'est ajouté par ce document.

## Chaîne Alembic observée

{chr(10).join(migration_rows)}

## Migrations structurantes

- Cycle des commandes : la migration `a1b2c3d4e5f6` remplace les anciens états par
  `DRAFT`, `CONFIRMED`, `SERVED`, `CANCELLED`, ajoute `payment_status` et conserve le downgrade destructif refusé.
- Ventilation des paiements : la migration `b2c3d4e5f6a7` ajoute `amount_presented`,
  `amount_applied` et `change_given`, avec reprise des lignes historiques depuis `amount`.
- Renforcement de l'intégrité : la migration terminale observée ajoute des CHECK de paiement,
  stock et mouvement, puis des FK composites vers les journaux financiers et les lignes de retour.

## Variantes SQLite / MySQL

- SQLite sert aux tests locaux et aux migrations vérifiées par la suite de schéma.
- MySQL/PyMySQL est la cible prévue pour la production ; ce document compile les types MySQL,
  mais ne doit pas être cité comme preuve d'une exécution serveur MySQL.
- Les colonnes binaires des jetons et sessions compilent en `BINARY` ou `VARBINARY` MySQL afin
  d'éviter les clés `BLOB` dans les index.
- Les colonnes calculées (`Computed`) sont déclarées dans les modèles ; leur syntaxe effective
  reste à valider sur le moteur cible quand une base MySQL réelle est testée.

## Diagramme relationnel dérivé des FK

Le diagramme ci-dessous ne contient que les relations issues des FK déclarées. Il ne décrit pas
les appels de service, les règles métier ni les écrans disponibles.

```mermaid
erDiagram
{_render_mermaid_edges()}
```

## Relations ORM déclarées

{_render_orm_relationships()}

## Relations SQL dérivées des FK

{_render_fk_table()}

## Dictionnaire physique
"""


def _render_mermaid_edges() -> str:
    lines = []
    for target, source, columns, _full_target, _name, _ondelete in _foreign_key_edges():
        label = columns.replace('"', "'")
        lines.append(f"    {target} ||--o{{ {source} : \"{label}\"")
    return "\n".join(lines) if lines else "    %% Aucune FK déclarée"


def _render_fk_table() -> str:
    rows = [
        "| Table source | Contrainte | Colonnes source | Cible | ON DELETE |",
        "| --- | --- | --- | --- | --- |",
    ]
    for _target, source, columns, full_target, name, ondelete in _foreign_key_edges():
        rows.append(f"| `{source}` | `{name}` | `{columns}` | `{full_target}` | `{ondelete}` |")
    return "\n".join(rows)


def _render_orm_relationships() -> str:
    rows = _orm_relationships()
    if not rows:
        return "Aucune relation ORM déclarée."
    output = [
        "| Table | Attribut | Cible | Direction | Cardinalité ORM |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source, key, target, direction, cardinality in rows:
        output.append(f"| `{source}` | `{key}` | `{target}` | `{direction}` | {cardinality} |")
    return "\n".join(output)


def _render_table(table) -> list[str]:
    output = [
        f"\n### `{table.name}`",
        "",
        "| Colonne | Type | NULL | PK | Default ORM | Default serveur | Calcul |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for column in table.columns:
        output.append(
            "| "
            + " | ".join(
                [
                    f"`{column.name}`",
                    _markdown(_type(column)),
                    "oui" if column.nullable else "non",
                    "oui" if column.primary_key else "non",
                    _markdown(_default(column.default)),
                    _markdown(_server_default(column)),
                    _markdown(_computed(column)),
                ]
            )
            + " |"
        )

    pk = next((constraint for constraint in table.constraints if isinstance(constraint, PrimaryKeyConstraint)), None)
    output.append("")
    output.append(f"- PK : `{_column_list(pk.columns)}`." if pk is not None else "- PK : —.")

    for constraint in sorted(table.constraints, key=_constraint_sort_key):
        if isinstance(constraint, PrimaryKeyConstraint):
            continue
        if isinstance(constraint, ForeignKeyConstraint):
            output.append(
                f"- FK `{constraint.name or '—'}` : `{_column_list(constraint.columns)}` → "
                f"`{_target_list(constraint)}` ; ON DELETE `{constraint.ondelete or '—'}`."
            )
        elif isinstance(constraint, UniqueConstraint):
            output.append(f"- UNIQUE `{constraint.name or '—'}` : `{_column_list(constraint.columns)}`.")
        elif isinstance(constraint, CheckConstraint):
            output.append(f"- CHECK `{constraint.name or '—'}` : `{constraint.sqltext}`.")

    for index in sorted(table.indexes, key=lambda item: item.name or ""):
        unique = " UNIQUE" if index.unique else ""
        output.append(f"- INDEX{unique} `{index.name}` : `{_column_list(index.columns)}`.")
    return output


def render() -> str:
    output = [_header()]
    for table in sorted(db.metadata.tables.values(), key=lambda item: item.name):
        output.extend(_render_table(table))

    output.append(
        """
## Contraintes SQL, validations de service et exigences futures

| Nature | Ce qui est couvert ici | Ce qui reste hors schéma |
| --- | --- | --- |
| Contraintes SQL | Types, nullabilité, PK, FK, UNIQUE, CHECK, index et colonnes calculées déclarés dans `app.models`. | Pas de trigger, pas de verrou métier généralisé, pas de cascade implicite non déclarée. |
| Validations de service | Les services peuvent imposer des règles plus fortes : décimaux finis, transitions d'état, plafonds cumulés, stock non négatif, transactions composées. | Ces règles doivent être vérifiées dans les services et les tests fonctionnels, pas déduites des tables seules. |
| Exigences futures | Les tables préparatoires peuvent soutenir idempotence, sessions, abonnements, dépenses ou rapports. | Une table sans route/service/test associé ne constitue pas une fonctionnalité livrée. |
"""
    )
    return "\n".join(output).rstrip() + "\n"


if __name__ == "__main__":
    (ROOT / "docs" / "DATA_MODEL.md").write_text(render(), encoding="utf-8")
