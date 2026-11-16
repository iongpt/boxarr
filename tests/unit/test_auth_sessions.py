"""Server-side sessions (#151, design §3.8.1, amendment A2).

Only SHA-256 digests are stored; lifetimes follow A2; last_seen is flushed
at most every 5 minutes; 50 sessions at most; a credential generation change
revokes; a corrupt file signs everyone out without locking.
"""

import json
import logging
import os
import stat

import pytest

from src.api.auth.sessions import (
    FLUSH_INTERVAL_SECONDS,
    IDLE_SECONDS,
    MAX_SESSIONS,
    REMEMBER_IDLE_SECONDS,
    SessionStore,
    token_digest,
)

HOUR = 3600
DAY = 24 * HOUR


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "auth-sessions.json"


def _create(store, remember=False, generation=0, hours=24, days=90):
    return store.create(
        remember=remember,
        generation=generation,
        session_hours=hours,
        remember_days=days,
    )


def _on_disk(path):
    return json.loads(path.read_text())


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_only_the_digest_is_stored_0600(path, clock):
    store = SessionStore.load(path, clock=clock)
    token, _ = _create(store)

    text = path.read_text()

    assert token not in text
    assert token_digest(token) in text
    assert list(_on_disk(path)["sessions"]) == [token_digest(token)]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_tokens_are_256_bit_and_unique(path, clock):
    store = SessionStore(path, clock=clock)
    tokens = {_create(store)[0] for _ in range(20)}

    assert len(tokens) == 20
    assert all(len(t) == 43 for t in tokens)  # token_urlsafe(32)


def test_sessions_survive_a_restart(path, clock):
    token, _ = _create(SessionStore.load(path, clock=clock))

    reloaded = SessionStore.load(path, clock=clock)

    assert reloaded.lookup(token, 0) is not None


def test_no_file_is_written_until_a_session_exists(path, clock):
    store = SessionStore.load(path, clock=clock)
    store.prune(0)
    store.flush()

    assert not path.exists()


# --------------------------------------------------------------------------
# Lifetimes (A2)
# --------------------------------------------------------------------------


def test_a_normal_session_has_a_12_hour_idle_timeout(path, clock):
    store = SessionStore(path, clock=clock)
    token, session = _create(store, hours=168)

    assert session.idle_s == IDLE_SECONDS == 12 * HOUR
    clock.advance(12 * HOUR)
    assert store.lookup(token, 0) is not None
    clock.advance(1)
    assert store.lookup(token, 0) is None
    assert len(store) == 0  # removed


def test_activity_slides_the_idle_timeout_up_to_the_cap(path, clock):
    store = SessionStore(path, clock=clock)
    token, session = _create(store, hours=24)

    assert session.expires == clock.now + 24 * HOUR
    for _ in range(2):
        clock.advance(11 * HOUR)  # within the idle timeout each time
        assert store.lookup(token, 0) is not None
        store.touch(token)
    clock.advance(2 * HOUR)  # 24 hours in: the cap, though 2 hours idle
    assert store.lookup(token, 0) is None


def test_keep_me_signed_in_lasts_30_idle_days_and_the_remember_cap(path, clock):
    store = SessionStore(path, clock=clock)
    token, session = _create(store, remember=True, days=90)

    assert session.remember
    assert session.idle_s == REMEMBER_IDLE_SECONDS == 30 * DAY
    assert session.expires == clock.now + 90 * DAY
    clock.advance(29 * DAY)
    assert store.lookup(token, 0) is not None
    store.touch(token)
    clock.advance(29 * DAY)
    assert store.lookup(token, 0) is not None
    store.touch(token)
    clock.advance(31 * DAY)  # 89 days in, but 31 idle
    assert store.lookup(token, 0) is None


def test_the_remember_cap_is_absolute(path, clock):
    store = SessionStore(path, clock=clock)
    token, _ = _create(store, remember=True, days=10)

    for _ in range(10):
        clock.advance(DAY)
        store.touch(token)
    assert store.lookup(token, 0) is None


def test_remember_days_0_turns_remember_off(path, clock):
    store = SessionStore(path, clock=clock)
    _, session = _create(store, remember=True, days=0, hours=8)

    assert not session.remember
    assert session.idle_s == IDLE_SECONDS
    assert session.expires == clock.now + 8 * HOUR


# --------------------------------------------------------------------------
# Revocation
# --------------------------------------------------------------------------


def test_a_generation_change_revokes_even_without_a_write(path, clock):
    store = SessionStore.load(path, clock=clock)
    token, _ = _create(store, generation=3)

    assert store.lookup(token, 3) is not None
    reloaded = SessionStore.load(path, clock=clock)  # the file still has it
    assert reloaded.lookup(token, 4) is None
    assert store.lookup(token, 4) is None


def test_revoke_and_revoke_all(path, clock):
    store = SessionStore.load(path, clock=clock)
    first, _ = _create(store)
    second, _ = _create(store)
    third, _ = _create(store)

    assert store.revoke(first) is True
    assert store.revoke(first) is False
    assert store.lookup(first, 0) is None
    assert token_digest(first) not in _on_disk(path)["sessions"]
    assert store.revoke_all() == 2
    assert store.lookup(second, 0) is None and store.lookup(third, 0) is None
    assert _on_disk(path)["sessions"] == {}


def test_prune_drops_invalid_sessions_only(path, clock):
    store = SessionStore(path, clock=clock)
    old, _ = _create(store, generation=1)
    stale, _ = _create(store, generation=2)
    clock.advance(13 * HOUR)
    fresh, _ = _create(store, generation=2)

    assert store.prune(2) == 2
    assert store.digests() == (token_digest(fresh),)


@pytest.mark.parametrize(
    "token",
    [None, 7, b"x" * 43, "", "short", "x" * 129, "has space" * 5, "é" * 40],
)
def test_odd_tokens_are_refused_quietly(path, clock, token):
    store = SessionStore(path, clock=clock)
    _create(store)

    assert store.lookup(token, 0) is None
    assert store.revoke(token) is False
    store.touch(token)
    assert len(store) == 1


def test_an_unknown_token_matches_nothing(path, clock):
    store = SessionStore(path, clock=clock)
    _create(store)

    assert store.lookup("A" * 43, 0) is None


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def test_last_seen_is_flushed_at_most_every_5_minutes(path, clock):
    store = SessionStore.load(path, clock=clock)
    token, session = _create(store)
    written = _on_disk(path)["sessions"][token_digest(token)]["last_seen"]

    clock.advance(60)
    store.touch(token)
    assert _on_disk(path)["sessions"][token_digest(token)]["last_seen"] == written

    clock.advance(FLUSH_INTERVAL_SECONDS)
    store.touch(token)
    assert _on_disk(path)["sessions"][token_digest(token)]["last_seen"] == clock.now


def test_flush_writes_pending_last_seen_at_shutdown(path, clock):
    store = SessionStore.load(path, clock=clock)
    token, _ = _create(store)
    clock.advance(30)
    store.touch(token)

    assert store.flush() is True
    assert _on_disk(path)["sessions"][token_digest(token)]["last_seen"] == clock.now
    assert store.flush() is False  # nothing pending


def test_at_most_50_sessions_least_recently_seen_go_first(path, clock):
    store = SessionStore.load(path, clock=clock)
    tokens = []
    for _ in range(MAX_SESSIONS):
        tokens.append(_create(store)[0])
        clock.advance(1)
    store.touch(tokens[0])  # the oldest is now the most recently seen
    clock.advance(1)

    newest, _ = _create(store)

    assert len(store) == MAX_SESSIONS == 50
    assert store.lookup(tokens[0], 0) is not None
    assert store.lookup(tokens[1], 0) is None
    assert store.lookup(newest, 0) is not None
    assert len(_on_disk(path)["sessions"]) == 50


def test_an_unwritable_directory_keeps_sessions_in_memory(tmp_path, clock, caplog):
    path = tmp_path / "missing-parent-is-a-file" / "auth-sessions.json"
    path.parent.write_text("a file, not a directory")
    store = SessionStore(path, clock=clock)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.sessions"):
        first, _ = _create(store)
        second, _ = _create(store)

    assert store.lookup(first, 0) is not None and store.lookup(second, 0) is not None
    warnings = [r for r in caplog.records if "memory only" in r.getMessage()]
    assert len(warnings) == 1  # once, not per write
    assert first not in caplog.text


def test_a_memory_only_store_works(clock):
    store = SessionStore(None, clock=clock)
    token, _ = _create(store)

    assert store.lookup(token, 0) is not None
    assert store.flush() is False


# --------------------------------------------------------------------------
# Corrupt files
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"version": 2, "sessions": {}}',
        b'{"version": 1, "sessions": []}',
        b'{"version": 1}',
        b"[" * 100000 + b"]" * 100000,
    ],
)
def test_a_corrupt_file_signs_everyone_out_and_is_moved_aside(
    path, clock, content, caplog
):
    path.write_bytes(content)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.sessions"):
        store = SessionStore.load(path, clock=clock)

    assert len(store) == 0
    assert not path.exists()
    assert (path.parent / "auth-sessions.json.broken").read_bytes() == content
    assert "signed out" in caplog.text
    token, _ = _create(store)  # and it carries on
    assert store.lookup(token, 0) is not None


def test_a_second_corrupt_file_gets_a_stamped_name(path, clock):
    path.write_bytes(b"first")
    SessionStore.load(path, clock=clock)
    path.write_bytes(b"second")
    SessionStore.load(path, clock=clock)

    names = sorted(p.name for p in path.parent.iterdir())
    assert names[0] == "auth-sessions.json.broken"
    assert names[1].startswith("auth-sessions.json.broken.")


def test_malformed_entries_are_dropped_and_the_rest_kept(path, clock, caplog):
    good_store = SessionStore.load(path, clock=clock)
    token, _ = _create(good_store)
    data = _on_disk(path)
    entry = data["sessions"][token_digest(token)]
    data["sessions"].update(
        {
            "not-a-digest": entry,
            "a" * 64: {**entry, "remember": "yes"},
            "b" * 64: {**entry, "idle_s": 0},
            "c" * 64: {**entry, "created": True},
            "d" * 64: "a string",
            "e" * 64: {k: v for k, v in entry.items() if k != "generation"},
        }
    )
    path.write_text(json.dumps(data))

    with caplog.at_level(logging.WARNING, logger="src.api.auth.sessions"):
        store = SessionStore.load(path, clock=clock)

    assert store.digests() == (token_digest(token),)
    assert "Ignored 6 malformed entries" in caplog.text
    assert path.exists()


def test_a_group_readable_sessions_file_is_tightened(path, clock, caplog):
    token, _ = _create(SessionStore.load(path, clock=clock))
    os.chmod(path, 0o644)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.sessions"):
        store = SessionStore.load(path, clock=clock)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "now 0600" in caplog.text
    assert store.lookup(token, 0) is not None


def test_expired_sessions_are_not_loaded(path, clock):
    store = SessionStore.load(path, clock=clock)
    _create(store, hours=1)
    clock.advance(2 * HOUR)

    assert len(SessionStore.load(path, clock=clock)) == 0
