"""Unit tests for RadarrService.get_languages()."""

from unittest.mock import Mock

import httpx
import pytest

import src.core.radarr as core_radarr
from src.core.radarr import RadarrService

# Shape of GET /api/v3/language: real languages carry a positive id, the
# Any/Original/Unknown pseudo-entries do not.
LANGUAGE_PAYLOAD = [
    {"id": -1, "name": "Any"},
    {"id": -2, "name": "Original"},
    {"id": 0, "name": "Unknown"},
    {"id": 1, "name": "English"},
    {"id": 10, "name": "Chinese"},
    {"id": 12, "name": "Norwegian"},
]


@pytest.fixture(autouse=True)
def clear_language_cache():
    """Keep the module-level TTL cache from leaking between tests."""
    core_radarr._languages_cache.clear()
    yield
    core_radarr._languages_cache.clear()


def _service(url="http://radarr-a:7878", api_key="key-a", payload=None, error=None):
    """Build a service backed by an injected HTTP client double."""
    client = Mock()
    if error is not None:
        client.request.side_effect = error
    else:
        response = Mock()
        response.status_code = 200
        response.json.return_value = LANGUAGE_PAYLOAD if payload is None else payload
        client.request.return_value = response
    service = RadarrService(url=url, api_key=api_key, http_client=client)
    return service, client


def test_returns_sorted_real_language_names():
    """Only id > 0 entries are returned, sorted for the picker."""
    service, _ = _service()
    assert service.get_languages() == ["Chinese", "English", "Norwegian"]


def test_requests_the_language_endpoint():
    """The vocabulary comes from Radarr's own language endpoint."""
    service, client = _service()
    service.get_languages()
    assert client.request.call_args[0] == ("GET", "/api/v3/language")


def test_connection_error_falls_back_to_empty_list():
    """A failure must not raise - callers fall back to the bundled list."""
    service, _ = _service(error=httpx.ConnectError("refused"))
    assert service.get_languages() == []


def test_malformed_payload_falls_back_to_empty_list():
    """A payload that is not a list of language objects is not fatal."""
    service, _ = _service(payload={"error": "nope"})
    assert service.get_languages() == []


def test_result_is_cached_per_instance():
    """A second call inside the TTL does not hit Radarr again."""
    service, client = _service()
    assert service.get_languages() == ["Chinese", "English", "Norwegian"]
    assert service.get_languages() == ["Chinese", "English", "Norwegian"]
    assert client.request.call_count == 1


def test_ignore_cache_refetches():
    """ignore_cache bypasses the TTL cache."""
    service, client = _service()
    service.get_languages()
    service.get_languages(ignore_cache=True)
    assert client.request.call_count == 2


def test_cache_is_keyed_per_instance():
    """A connection test against another Radarr must not poison the cache.

    POST /api/config/test builds throwaway services against arbitrary URLs, so
    a cache keyed by nothing would serve instance B's languages for instance A.
    """
    service_a, client_a = _service(
        url="http://radarr-a:7878",
        api_key="key-a",
        payload=[{"id": 1, "name": "English"}],
    )
    service_b, client_b = _service(
        url="http://radarr-b:7878",
        api_key="key-b",
        payload=[{"id": 2, "name": "French"}],
    )

    assert service_a.get_languages() == ["English"]
    assert service_b.get_languages() == ["French"]
    # Instance A still serves its own list, from cache.
    assert service_a.get_languages() == ["English"]
    assert client_a.request.call_count == 1
    assert client_b.request.call_count == 1
