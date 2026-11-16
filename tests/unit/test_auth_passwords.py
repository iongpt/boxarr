"""Password hashing (#151, design §3.1.4): scrypt, strict bounded format.

The format has no ``$`` (a compose file would interpolate it), carries its
own parameters, and is parsed strictly: anything malformed or outside the
bounds is a HashFormatError - which locks the instance - so a hand-edited
hash can neither crash Boxarr nor make it allocate unbounded memory.
"""

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from src.api.auth import passwords
from src.api.auth.passwords import (
    DEFAULT_LOG2N,
    DEFAULT_P,
    DEFAULT_R,
    MAX_PASSWORD_BYTES,
    HashFormatError,
    ScryptHash,
    dummy_hash,
    hash_password,
    needs_rehash,
    parse_hash,
    password_problem,
    username_fold,
    username_problem,
    verify_dummy,
    verify_password,
)

PASSWORD = "correct horse battery"  # a test value, not a real password


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _cheap_hash(password: str, log2n: int = 12, r: int = 8, p: int = 1) -> str:
    """A valid hash with cheap parameters, made without the module."""
    salt = b"s" * 16
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=1 << log2n,
        r=r,
        p=p,
        maxmem=128 * 1024 * 1024,
        dklen=32,
    )
    return f"scrypt:{log2n}:{r}:{p}:{_b64(salt)}:{_b64(key)}"


# --------------------------------------------------------------------------
# The format
# --------------------------------------------------------------------------


def test_a_hash_has_the_colon_format_and_no_dollar():
    encoded = hash_password(PASSWORD)

    assert "$" not in encoded
    assert "=" not in encoded  # unpadded base64url
    parts = encoded.split(":")
    assert parts[:4] == ["scrypt", str(DEFAULT_LOG2N), str(DEFAULT_R), str(DEFAULT_P)]
    parsed = parse_hash(encoded)
    assert len(parsed.salt) == 16 and len(parsed.key) == 32
    assert parsed.encode() == encoded


def test_the_current_parameters_fit_the_bounds_and_16_mib():
    assert 128 * DEFAULT_R * (1 << DEFAULT_LOG2N) == 16 * 1024 * 1024
    assert (DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P) == (14, 8, 2)


def test_each_hash_gets_its_own_salt():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_verify_accepts_the_password_and_nothing_else():
    encoded = hash_password(PASSWORD)

    assert verify_password(PASSWORD, encoded)
    assert not verify_password(PASSWORD + " ", encoded)
    assert not verify_password(PASSWORD.upper(), encoded)
    assert not verify_password("", encoded)
    assert not verify_password(None, encoded)
    assert not verify_password(b"correct horse battery", encoded)


def test_passwords_are_nfkc_normalised():
    # The stored form is NFKC; "\ufb01" (the fi ligature) folds to "fi".
    encoded = _cheap_hash("file-cabinet-9")

    assert verify_password("file-cabinet-9", encoded)
    assert verify_password("\ufb01le-cabinet-9", encoded)
    assert verify_password("file-cabinet-9", hash_password("\ufb01le-cabinet-9"))


def test_a_stored_hash_with_other_parameters_still_verifies():
    encoded = _cheap_hash(PASSWORD, log2n=12, r=4, p=1)

    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong password!", encoded)


def test_an_oversized_password_is_refused_before_hashing(monkeypatch):
    encoded = _cheap_hash(PASSWORD)
    calls = []
    monkeypatch.setattr(passwords, "_derive", lambda *a, **k: calls.append(1) or b"")

    assert not verify_password("x" * (MAX_PASSWORD_BYTES + 1), encoded)
    assert not verify_password("é" * (MAX_PASSWORD_BYTES // 2 + 1), encoded)
    assert calls == []
    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))
    assert calls == []


def _count_scrypt(monkeypatch):
    """Record the (n, r, p, maxmem, dklen) of every hashlib.scrypt call."""
    runs = []
    real = hashlib.scrypt

    def spy(password, *, salt, n, r, p, maxmem=0, dklen=64):
        runs.append((n, r, p, maxmem, dklen))
        return real(password, salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=dklen)

    monkeypatch.setattr(passwords.hashlib, "scrypt", spy)
    return runs


def test_the_dummy_hash_costs_nothing_to_make_and_always_fails(monkeypatch):
    monkeypatch.setattr(passwords, "_dummies", {})
    runs = _count_scrypt(monkeypatch)

    made = dummy_hash()

    assert runs == []  # a random salt and key: no scrypt to make it
    assert isinstance(made, ScryptHash)
    assert (made.log2n, made.r, made.p) == (DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P)
    assert (len(made.salt), len(made.key)) == (16, 32)
    assert dummy_hash() is made  # kept
    assert verify_dummy(PASSWORD) is False
    assert len(runs) == 1


def test_nothing_is_made_at_import():
    code = (
        "import hashlib\n"
        "calls = []\n"
        "real = hashlib.scrypt\n"
        "hashlib.scrypt = lambda *a, **k: calls.append(1) or real(*a, **k)\n"
        "import src.api.auth.passwords as p\n"
        "assert p._dummies == {} and calls == [], (p._dummies, calls)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).parents[2],
    )

    assert done.returncode == 0, done.stderr


def test_the_dummy_check_does_the_same_work(monkeypatch):
    runs = _count_scrypt(monkeypatch)

    verify_dummy(PASSWORD)

    assert [run[:3] for run in runs] == [(1 << DEFAULT_LOG2N, DEFAULT_R, DEFAULT_P)]


@pytest.mark.parametrize(
    "stored",
    [
        _cheap_hash(PASSWORD, log2n=12, r=8, p=1),
        _cheap_hash(PASSWORD, log2n=13, r=4, p=3),
        _cheap_hash(PASSWORD, log2n=12, r=8, p=5),
    ],
)
def test_an_unknown_username_costs_what_the_configured_hash_costs(monkeypatch, stored):
    """MF8: the dummy check uses the configured hash's parameters, not the
    defaults, so the time does not tell whether the username exists."""
    runs = _count_scrypt(monkeypatch)

    assert verify_password(PASSWORD, stored) is True
    verify_dummy("a wrong guess", like=stored)
    verify_dummy("a wrong guess", like=parse_hash(stored))

    assert len(runs) == 3 and runs[0] == runs[1] == runs[2]
    assert dummy_hash(parse_hash(stored)) is dummy_hash(parse_hash(stored))
    assert dummy_hash(parse_hash(stored)).key != parse_hash(stored).key


def test_a_dummy_keeps_the_salt_and_key_sizes():
    salt, key = b"s" * 24, b"k" * 48
    like = ScryptHash(12, 8, 1, salt, key)

    dummy = dummy_hash(like)

    assert (len(dummy.salt), len(dummy.key)) == (24, 48)
    assert dummy.salt != salt and dummy.key != key


def test_a_malformed_configured_hash_raises_like_a_real_check():
    with pytest.raises(HashFormatError):
        verify_dummy(PASSWORD, like="scrypt:99:8:1:x:y")


def test_verify_compares_in_constant_time(monkeypatch):
    calls = []
    real = passwords.hmac.compare_digest
    monkeypatch.setattr(
        passwords.hmac,
        "compare_digest",
        lambda a, b: calls.append((len(a), len(b))) or real(a, b),
    )
    stored = _cheap_hash(PASSWORD)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong password!", stored) is False

    assert calls == [(32, 32), (32, 32)]


# --------------------------------------------------------------------------
# The strict parser
# --------------------------------------------------------------------------

GOOD = _cheap_hash(PASSWORD)
SALT = GOOD.split(":")[4]
KEY = GOOD.split(":")[5]


@pytest.mark.parametrize(
    "encoded",
    [
        f"scrypt:11:8:1:{SALT}:{KEY}",  # log2N below 12
        f"scrypt:18:1:1:{SALT}:{KEY}",  # log2N above 17
        f"scrypt:14:0:1:{SALT}:{KEY}",  # r below 1
        f"scrypt:14:17:1:{SALT}:{KEY}",  # r above 16
        f"scrypt:14:8:0:{SALT}:{KEY}",  # p below 1
        f"scrypt:14:8:17:{SALT}:{KEY}",  # p above 16
        f"scrypt:17:8:1:{SALT}:{KEY}",  # 128 MiB
        f"scrypt:16:16:1:{SALT}:{KEY}",  # 128 MiB
        f"scrypt:17:16:16:{SALT}:{KEY}",  # everything at the top
        f"scrypt:30:8:1:{SALT}:{KEY}",  # 2^30: would be 128 GiB
        f"scrypt:999:8:1:{SALT}:{KEY}",
        f"scrypt:14:8:99999999:{SALT}:{KEY}",
    ],
)
def test_out_of_bounds_parameters_are_refused(encoded):
    with pytest.raises(HashFormatError):
        parse_hash(encoded)


def test_the_largest_hash_in_bounds_is_accepted():
    assert parse_hash(f"scrypt:17:4:16:{SALT}:{KEY}").log2n == 17
    assert parse_hash(f"scrypt:12:16:16:{SALT}:{KEY}").r == 16


def test_a_64_mib_hash_verifies():
    """128 * r * N = 64 MiB, past OpenSSL's default 32 MiB maxmem: the check
    must raise the limit, or every such hash the parser accepts would raise
    at login instead of verifying."""
    stored = _cheap_hash(PASSWORD, log2n=17, r=4, p=1)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password("not the password", stored) is False


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "scrypt",
        f"$scrypt$14$8$1${SALT}${KEY}",  # the $ format is not accepted
        f"scrypt$14$8$1${SALT}${KEY}",
        f"bcrypt:14:8:1:{SALT}:{KEY}",
        f"SCRYPT:14:8:1:{SALT}:{KEY}",
        f"scrypt:14:8:1:{SALT}",
        f"scrypt:14:8:1:{SALT}:{KEY}:extra",
        f"scrypt:014:8:1:{SALT}:{KEY}",  # leading zero
        f"scrypt:+14:8:1:{SALT}:{KEY}",
        f"scrypt: 14:8:1:{SALT}:{KEY}",
        f"scrypt:14.0:8:1:{SALT}:{KEY}",
        f"scrypt:-1:8:1:{SALT}:{KEY}",
        f"scrypt:１４:8:1:{SALT}:{KEY}",  # fullwidth digits
        f"scrypt:14:8:1:{SALT}=:{KEY}",  # padding
        f"scrypt:14:8:1:{SALT.replace('-', '+')}+/:{KEY}",  # not url-safe
        f"scrypt:14:8:1:c2FsdA:{KEY}",  # a 4-byte salt
        f"scrypt:14:8:1:{_b64(b's' * 65)}:{KEY}",  # a 65-byte salt
        f"scrypt:14:8:1:{SALT}:{_b64(b'k' * 8)}",  # an 8-byte key
        f"scrypt:14:8:1:{SALT}:{_b64(b'k' * 65)}",
        f"scrypt:14:8:1:{SALT}:{KEY}" + "A" * 300,
        f"scrypt:14:8:1:{SALT}:{KEY[:-1]}B",  # non-canonical trailing bits
    ],
)
def test_malformed_hashes_are_refused(encoded):
    with pytest.raises(HashFormatError):
        parse_hash(encoded)


@pytest.mark.parametrize("value", [None, 14, b"scrypt:14:8:1:x:y", ["scrypt"]])
def test_non_text_is_refused(value):
    with pytest.raises(HashFormatError):
        parse_hash(value)


def test_errors_never_quote_the_hash():
    encoded = f"scrypt:30:8:1:{SALT}:{KEY}"
    with pytest.raises(HashFormatError) as caught:
        parse_hash(encoded)
    assert SALT not in str(caught.value) and KEY not in str(caught.value)
    assert SALT not in repr(parse_hash(GOOD)) and KEY not in repr(parse_hash(GOOD))


def test_verify_raises_on_a_malformed_stored_hash():
    with pytest.raises(HashFormatError):
        verify_password(PASSWORD, f"scrypt:30:8:1:{SALT}:{KEY}")


# --------------------------------------------------------------------------
# Rehash and rules
# --------------------------------------------------------------------------


def test_rehash_only_for_ui_hashes_with_other_parameters():
    old = _cheap_hash(PASSWORD)
    current = hash_password(PASSWORD)

    assert needs_rehash(old, "ui")
    assert not needs_rehash(old, "env")
    assert not needs_rehash(current, "ui")


@pytest.mark.parametrize(
    "password, username, ok",
    [
        ("twelve chars", None, True),
        ("eleven char", None, False),
        ("x" * MAX_PASSWORD_BYTES, None, True),
        ("x" * (MAX_PASSWORD_BYTES + 1), None, False),
        ("é" * 600, None, False),  # 1200 bytes
        ("administrator", "administrator", False),
        ("ADMINISTRATOR", "administrator", False),
        ("administrator", "Administrator ", False),
        ("administrators", "administrator", True),
        (None, None, False),
    ],
)
def test_password_rules(password, username, ok):
    assert (password_problem(password, username) is None) is ok


def test_password_problems_never_quote_the_password():
    problem = password_problem("short-pw")
    assert problem and "short-pw" not in problem


@pytest.mark.parametrize(
    "username, ok",
    [
        ("admin", True),
        (" admin ", True),
        ("Zoë", True),
        ("", False),
        ("   ", False),
        ("a" * 64, True),
        ("a" * 65, False),
        ("ad\nmin", False),
        ("ad​min", False),  # zero-width space (format character)
        (None, False),
    ],
)
def test_username_rules(username, ok):
    assert (username_problem(username) is None) is ok


def test_usernames_compare_casefolded_and_normalised():
    assert username_fold("Admin") == username_fold("ADMIN ") == username_fold("admin")
    assert username_fold("Straße") == username_fold("STRASSE")
    assert username_fold("ｆｕｌｌ") == username_fold("full")
