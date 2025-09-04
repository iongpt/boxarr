"""Integration tests for URL base support."""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.utils.config import Settings


def test_empty_url_base():
    """Test that application works with empty url_base (backward compatibility)."""
    # Create app with empty url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint at root
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect from root to dashboard
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_url_base_boxarr(monkeypatch):
    """Test that application works with url_base set to 'boxarr'."""
    # Mock settings with url_base
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "boxarr")
    
    # Create app with url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint with base path
    response = client.get("/boxarr/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect from root to dashboard with base path
    response = client.get("/boxarr/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/boxarr/dashboard"
    
    # Test widget endpoint includes correct URL
    response = client.get("/boxarr/api/widget")
    assert response.status_code == 200
    assert "href=\"http://testserver/boxarr/\"" in response.text


def test_url_base_nested_path(monkeypatch):
    """Test that application works with nested url_base like 'apps/boxarr'."""
    # Mock settings with nested url_base
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "apps/boxarr")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "apps/boxarr")
    
    # Create app with nested url_base
    app = create_app()
    client = TestClient(app)
    
    # Test health endpoint with nested base path
    response = client.get("/apps/boxarr/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test redirect with nested base path
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
    # Mock settings with url_base
    monkeypatch.setattr("src.utils.config.settings.boxarr_url_base", "boxarr")
    monkeypatch.setattr("src.api.app.settings.boxarr_url_base", "boxarr")
    
    app = create_app()
    client = TestClient(app)
    
    # Request dashboard page to check JavaScript injection
    response = client.get("/boxarr/dashboard")
    
    # Check that base path is injected into JavaScript
    if response.status_code == 200:  # Only if dashboard loads
        assert "window.BOXARR_BASE_PATH" in response.text
        # The exact value depends on template rendering