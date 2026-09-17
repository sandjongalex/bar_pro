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
