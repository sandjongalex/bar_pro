from pathlib import Path

from jinja2 import Environment


def test_navigation_exposes_staff_on_duty_link():
    template = Path("app/templates/_app_navigation.html").read_text(encoding="utf-8")
    Environment().parse(template)

    assert "Personnel en service" in template
    assert "shifts_web.manage" in template
    assert "nav_can('shifts.read', nav_bar_id)" in template
    assert "endpoint.startswith('shifts_web.')" in template
