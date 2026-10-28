import pytest

from pixkeep import crypto


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(crypto.config, "TOKEN_KEY", "correct horse battery staple")


def test_roundtrip(key):
    enc = crypto.encrypt("ya29.secret")
    assert enc.startswith("enc:") and "secret" not in enc
    assert crypto.decrypt(enc) == "ya29.secret"
    assert crypto.encrypt(enc) == enc            # never double-encrypted


def test_plaintext_passes_through(key):
    assert crypto.decrypt("1//legacy-plain") == "1//legacy-plain"


def test_without_key_nothing_is_encrypted(monkeypatch):
    monkeypatch.setattr(crypto.config, "TOKEN_KEY", None)
    assert crypto.encrypt("tok") == "tok"


def test_wrong_or_missing_key_is_a_clear_error(monkeypatch, key):
    enc = crypto.encrypt("tok")
    monkeypatch.setattr(crypto.config, "TOKEN_KEY", "another key")
    with pytest.raises(crypto.TokenKeyError, match="не подходит"):
        crypto.decrypt(enc)
    monkeypatch.setattr(crypto.config, "TOKEN_KEY", None)
    with pytest.raises(crypto.TokenKeyError, match="не задан"):
        crypto.decrypt(enc)
