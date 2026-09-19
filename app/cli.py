"""Safe operational commands exposed through Flask CLI."""

import click
from flask import Flask
from sqlalchemy import select

from app.extensions import db
from app.models import Bar, StaffAssignment, User


def register_cli(app: Flask) -> None:
    @app.cli.command("create-superadmin")
    @click.option("--email", envvar="SUPERADMIN_EMAIL", prompt=True)
    @click.option("--display-name", envvar="SUPERADMIN_DISPLAY_NAME", prompt=True)
    @click.password_option(envvar="SUPERADMIN_PASSWORD", confirmation_prompt=True)
    def create_superadmin(email: str, display_name: str, password: str) -> None:
        """Create the first global administrator without a default password."""
        from sqlalchemy import select
        from app.extensions import db
        from app.models import User
        normalized = email.strip().lower()
        if db.session.scalar(select(User.id).where(User.email == normalized)):
            raise click.ClickException("Cette adresse existe déjà.")
        user = User(email=normalized, display_name=display_name.strip(), category="SUPER_ADMIN")
        user.set_password(password)
        db.session.add(user); db.session.commit()
        click.echo("Super-administrateur créé.")

    @app.cli.command("check-config")
    def check_config() -> None:
        """Verify that the selected configuration passed validation."""
        click.echo(f"Configuration {app.config['ENVIRONMENT']} validée.")

    @app.cli.command("seed-default-catalog")
    @click.option("--bar-id", type=int, help="Identifiant d'un établissement existant.")
    @click.option("--all-bars", is_flag=True, help="Appliquer le catalogue à tous les établissements.")
    def seed_default_catalog_command(bar_id: int | None, all_bars: bool) -> None:
        """Attach every missing default product to one or all existing bars."""
        from app.default_catalog import DEFAULT_CATALOG_CURRENCY, seed_default_catalog

        if (bar_id is None) == (not all_bars):
            raise click.ClickException("Utilisez soit --bar-id ID, soit --all-bars.")

        if all_bars:
            bars = list(db.session.scalars(select(Bar).order_by(Bar.id)))
        else:
            bar = db.session.get(Bar, bar_id)
            if not bar:
                raise click.ClickException("Établissement introuvable.")
            bars = [bar]

        total_categories = 0
        total_products = 0
        try:
            for bar in bars:
                result = seed_default_catalog(bar.id)
                total_categories += result["categories_created"]
                total_products += result["products_created"]
                click.echo(
                    f"{bar.id} - {bar.name}: "
                    f"{result['categories_created']} catégorie(s), "
                    f"{result['products_created']} produit(s) ajouté(s)."
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        click.echo(
            f"Terminé : {total_categories} catégorie(s) et {total_products} produit(s) créés. "
            f"Tarif de référence : {DEFAULT_CATALOG_CURRENCY}."
        )

    @app.cli.command("rebuild-stock-valuations")
    @click.option("--bar-id", type=int, help="Identifiant d'un établissement existant.")
    @click.option("--all-bars", is_flag=True, help="Analyser tous les établissements.")
    @click.option("--apply", is_flag=True, help="Appliquer les corrections fiables. Sans cette option, aucune écriture.")
    @click.option(
        "--include-estimates",
        is_flag=True,
        help="Avec --apply, autoriser aussi les estimations de coût héritées d'un historique ancien incomplet.",
    )
    def rebuild_stock_valuations_command(
        bar_id: int | None,
        all_bars: bool,
        apply: bool,
        include_estimates: bool,
    ) -> None:
        """Preview or repair moving weighted-average product valuations."""
        from app.stock_valuation import rebuild_bar_valuations

        if (bar_id is None) == (not all_bars):
            raise click.ClickException("Utilisez soit --bar-id ID, soit --all-bars.")
        if include_estimates and not apply:
            raise click.ClickException("--include-estimates doit être utilisé avec --apply.")

        if all_bars:
            bars = list(db.session.scalars(select(Bar).order_by(Bar.id)))
        else:
            bar = db.session.get(Bar, bar_id)
            if not bar:
                raise click.ClickException("Établissement introuvable.")
            bars = [bar]

        grand_changed = 0
        grand_estimated = 0
        grand_applied = 0
        grand_skipped = 0
        try:
            for bar in bars:
                result = rebuild_bar_valuations(
                    bar.id,
                    apply=apply,
                    include_estimates=include_estimates,
                )
                grand_changed += result["changed"]
                grand_estimated += result["estimated_changed"]
                grand_applied += result["applied_count"]
                grand_skipped += result["skipped"]
                click.echo(f"\n{bar.id} - {bar.name}")
                for row in result["rows"]:
                    if row["status"] not in {
                        "CHANGED",
                        "ESTIMATED_CHANGE",
                        "QUANTITY_MISMATCH",
                        "NO_COST_HISTORY",
                    }:
                        continue
                    product = row["product"]
                    click.echo(
                        f"  {product.id} · {product.name}: {row['status']} | "
                        f"stock={row['balance_quantity']} | "
                        f"ancien={row['old_unit_cost']} | nouveau={row['new_unit_cost']}"
                    )
                    if row["valuation_basis"] == "ESTIMATED_LEGACY":
                        reasons = ", ".join(row["estimate_reasons"]) or "historique ancien incomplet"
                        click.echo(f"    estimation: {reasons}")
                    if row["anomaly"]:
                        click.echo(f"    anomalie: {row['anomaly']}")
                click.echo(
                    f"  Résumé: {result['products']} produit(s), "
                    f"{result['changed']} coût(s) à corriger dont "
                    f"{result['estimated_changed']} estimation(s), "
                    f"{result['applied_count']} appliqué(s), "
                    f"{result['skipped']} ignoré(s)."
                )

            if apply:
                db.session.commit()
                click.echo(
                    f"\nApplication terminée : {grand_applied} coût(s) mis à jour sur "
                    f"{grand_changed} correction(s) proposée(s), "
                    f"{grand_estimated} estimation(s), {grand_skipped} produit(s) ignoré(s)."
                )
                if grand_estimated and not include_estimates:
                    click.echo(
                        "Les estimations legacy n'ont pas été écrites. "
                        "Après vérification, utilisez aussi --include-estimates si vous souhaitez les appliquer."
                    )
            else:
                db.session.rollback()
                click.echo(
                    f"\nAPERÇU UNIQUEMENT : {grand_changed} coût(s) seraient à corriger, "
                    f"dont {grand_estimated} estimation(s) legacy. Aucune donnée n'a été modifiée."
                )
        except Exception:
            db.session.rollback()
            raise

    @app.cli.command("seed-demo")
    def seed_demo() -> None:
        """Insert clearly fictional tenant-isolation data in non-production only."""
        if app.config["ENVIRONMENT"] == "production":
            raise click.ClickException("seed-demo est interdit en production.")
        if db.session.scalar(select(User.id).where(User.email == "owner.alpha.demo@example.invalid")):
            click.echo("Les données DEMO existent déjà; aucune écriture effectuée.")
            return
        alpha = User(email="owner.alpha.demo@example.invalid", display_name="DEMO Owner Alpha", category="OWNER")
        beta = User(email="owner.beta.demo@example.invalid", display_name="DEMO Owner Beta", category="OWNER")
        cashier = User(email="cashier.alpha.demo@example.invalid", display_name="DEMO Cashier Alpha", category="EMPLOYEE")
        for user in (alpha, beta, cashier):
            user.set_password("DEMO-NOT-FOR-PRODUCTION")
        db.session.add_all((alpha, beta, cashier)); db.session.flush()
        alpha_one = Bar(owner_id=alpha.id, name="DEMO Alpha One", timezone="Africa/Douala", currency="XAF")
        alpha_two = Bar(owner_id=alpha.id, name="DEMO Alpha Two", timezone="Africa/Douala", currency="XAF")
        beta_one = Bar(owner_id=beta.id, name="DEMO Beta One", timezone="Africa/Douala", currency="XAF")
        db.session.add_all((alpha_one, alpha_two, beta_one)); db.session.flush()
        db.session.add(StaffAssignment(bar_id=alpha_one.id, user_id=cashier.id, role="CASHIER", started_at=__import__("app.models", fromlist=["utcnow"]).utcnow()))
        db.session.commit()
        click.echo("Données DEMO créées : 2 propriétaires, 3 bars et 1 caissier fictifs.")
