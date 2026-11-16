"""An invalid radarr api_key in local.yaml is never logged (#151, §3.8.5).

The loader ignores an invalid value and logs why. pydantic's message quotes
the value (``input_value=...``), which for the Radarr API key is the secret
itself: for that field the warning names the field and the error type only.
Every other field keeps the full message.
"""

import logging

import pytest
import yaml

from src.utils.config import Settings

# Obviously fake: the shape of a key, not a real one.
FAKE_NUMERIC_KEY = 31415926535897932384626433832795
FAKE_KEY_PART = "fake-radarr-key-in-a-list-0451"


def _load(tmp_path, data, caplog):
    path = tmp_path / "local.yaml"
    path.write_text(yaml.safe_dump(data))
    settings = Settings(boxarr_data_directory=tmp_path)
    with caplog.at_level(logging.WARNING, logger="src.utils.config"):
        settings.load_from_yaml(path)
    return settings


@pytest.mark.parametrize(
    "bad_key, secret_text",
    [
        (FAKE_NUMERIC_KEY, str(FAKE_NUMERIC_KEY)),
        ([FAKE_KEY_PART], FAKE_KEY_PART),
        ({"value": FAKE_KEY_PART}, FAKE_KEY_PART),
    ],
    ids=["number", "list", "mapping"],
)
def test_the_warning_names_the_field_and_error_type_only(
    tmp_path, caplog, bad_key, secret_text
):
    settings = _load(tmp_path, {"radarr": {"api_key": bad_key}}, caplog)

    assert settings.radarr_api_key == ""  # ignored, as before
    warnings = [r for r in caplog.records if "radarr_api_key" in r.getMessage()]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "Ignoring invalid value for 'radarr_api_key'" in message
    assert "_type" in message  # the pydantic error type, e.g. string_type
    assert secret_text not in caplog.text
    assert "input_value" not in caplog.text


def test_other_fields_keep_the_full_message(tmp_path, caplog):
    _load(tmp_path, {"boxarr": {"port": "not-a-port"}}, caplog)

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "boxarr_port" in m and "input_value='not-a-port'" in m for m in messages
    ), messages


def test_a_valid_key_still_loads(tmp_path, caplog):
    settings = _load(tmp_path, {"radarr": {"api_key": FAKE_KEY_PART}}, caplog)

    assert settings.radarr_api_key == FAKE_KEY_PART
    assert not [r for r in caplog.records if "radarr_api_key" in r.getMessage()]
