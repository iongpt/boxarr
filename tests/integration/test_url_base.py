"""Integration tests for URL base support."""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.utils.config import Settings


def test_empty_url_base(monkeypatch):
    """Test that application works with empty url_base (backward compatibility)."""
    # Mock unconfigured state (no API key)
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "")
    
    # Create app with empty url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint at root
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect from root to setup when unconfigured
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/setup"
    
    # Test with configured state
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "test_key")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "test_key")
    
    # Recreate app with configuration
    app = create_app()
    client = TestClient(app)
    
    # Now should redirect to dashboard
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_url_base_boxarr(monkeypatch):
    """Test that application works with url_base set to 'boxarr'."""
    # Mock settings with url_base and unconfigured state
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "")
    
    # Create app with url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint with base path
    response = client.get("/boxarr/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect from root to setup with base path (unconfigured)
    response = client.get("/boxarr/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/boxarr/setup"
    
    # Test with configured state
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "test_key")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "test_key")
    
    # Recreate app
    app = create_app()
    client = TestClient(app)
    
    # Now should redirect to dashboard
    response = client.get("/boxarr/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/boxarr/dashboard"
    
    # Test widget endpoint includes correct URL
    response = client.get("/boxarr/api/widget")
    assert response.status_code == 200
    assert "href=\"http://testserver/boxarr/\"" in response.text


def test_url_base_nested_path(monkeypatch):
    """Test that application works with nested url_base like 'apps/boxarr'."""
    # Mock settings with nested url_base and configured state
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "apps/boxarr")
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "test_key")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "apps/boxarr")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "test_key")
    
    # Create app with nested url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint with nested base path
    response = client.get("/apps/boxarr/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect with nested base path (configured, goes to dashboard)
    response = client.get("/apps/boxarr/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/apps/boxarr/dashboard"


def test_url_base_normalization(monkeypatch):
    """Test that url_base is normalized correctly (strips slashes)."""
    test_cases = [
        ("/boxarr/", "boxarr"),
        ("boxarr/", "boxarr"),
        ("/boxarr", "boxarr"),
        ("//boxarr//", "boxarr"),
        ("/apps/boxarr/", "apps/boxarr"),
    ]
    
    for input_base, expected_normalized in test_cases:
        # Test normalization in settings validator
        settings = Settings(boxarr_url_base=input_base)
        assert settings.boxarr_url_base == expected_normalized


def test_javascript_base_path_injection(monkeypatch):
    """Test that base path is correctly injected for JavaScript."""
    # Mock settings with url_base and unconfigured state to go to setup
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.utils.config.settings.radarr_api_key", "")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.api.routes.web.settings.radarr_api_key", "")
    
    app = create_app()
    client = TestClient(app)
    
    # Request setup page (since we're unconfigured)
    response = client.get("/boxarr/setup")
    
    # Check that base path is injected into JavaScript
    assert response.status_code == 200
    assert "window.BOXARR_BASE_PATH" in response.text
    assert '/boxarr' in response.text  # Should see the base path somewhere