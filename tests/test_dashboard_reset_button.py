from pathlib import Path

from jinja2 import Environment


def test_dashboard_exposes_guarded_reset_action():
    template = Path("app/templates/dashboard.html").read_text(encoding="utf-8")

    # Parse the complete Jinja template so a malformed modal cannot reach production.
    Environment().parse(template)

    assert "Réinitialiser le bar" in template
    assert "bars.web_reset" in template
    assert "nav_can('bars.reset', active_bar.id)" in template
    assert 'name="confirmation"' in template
    assert 'name="password"' in template
