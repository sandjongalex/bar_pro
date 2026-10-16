"""PWA contract: installable shell without offline caching of private data."""
from pathlib import Path
import json
from app import create_app

ROOT = Path(__file__).resolve().parents[1]

def test_manifest_and_service_worker_contract():
    manifest=json.loads((ROOT/"app/static/manifest.webmanifest").read_text())
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    worker=(ROOT/"app/static/sw.js").read_text()
    assert "request.mode !== 'navigate'" in worker
    assert "request.method !== 'GET'" in worker
    assert "caches.match('/static/offline.html')" in worker
    assert "cache.addAll(SAFE_ASSETS)" in worker
    assert "cache.put(" not in worker
    assert "/dashboard" not in worker

def test_worker_is_root_scoped_and_uncached():
    app=create_app("testing", {"SECRET_KEY":"pwa-tests", "WTF_CSRF_ENABLED":False})
    client=app.test_client()
    response=client.get("/sw.js")
    assert response.status_code==200
    assert response.mimetype=="application/javascript"
    assert response.headers["Service-Worker-Allowed"]=="/"
    assert "no-store" in response.headers["Cache-Control"]
    assert b"self.addEventListener('fetch'" in response.data
    manifest=client.get("/static/manifest.webmanifest")
    assert manifest.status_code==200

def test_both_login_and_authenticated_shell_include_install_support():
    for name in ("layout.html","login.html"):
        content=(ROOT/"app/templates"/name).read_text()
        assert "manifest.webmanifest" in content
        assert "pwa.js" in content
        assert "apple-mobile-web-app" in content
