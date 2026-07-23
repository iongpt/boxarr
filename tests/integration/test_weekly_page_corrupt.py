"""Integration test: a corrupt weekly JSON file degrades gracefully, not 500."""

from fastapi.testclient import TestClient

from src.api.app import create_app


def test_serve_weekly_page_with_corrupt_json_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.config.settings.boxarr_data_directory", tmp_path)

    weekly_pages_dir = tmp_path / "weekly_pages"
    weekly_pages_dir.mkdir(parents=True)
    # Simulate a truncated/interrupted write.
    (weekly_pages_dir / "2024W10.json").write_text('{"year": 2024, "week": 10, "mov')

    app = create_app()
    # raise_server_exceptions defaults to True, so an unhandled 500 would raise.
    client = TestClient(app)

    response = client.get("/2024W10")

    assert response.status_code == 404
    assert "corrupt" in response.json()["detail"].lower()
