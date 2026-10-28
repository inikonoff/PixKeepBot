"""
Encryption of Google tokens at rest (Fernet: AES-128-CBC + HMAC).

The key comes from the TOKEN_KEY environment variable — any long random
string; a Fernet key is derived from it with SHA-256. Encrypted values are
stored as "enc:<fernet token>", so plaintext rows written before the key was
set are still readable and get encrypted at startup (encrypt_existing_tokens
in db.py). Without TOKEN_KEY tokens are stored as plaintext, as before.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from pixkeep.config import config

PREFIX = "enc:"


class TokenKeyError(RuntimeError):
    """Stored tokens can't be decrypted: TOKEN_KEY is missing or different."""


def _fernet() -> Fernet | None:
    if not config.TOKEN_KEY:
        return None
    digest = hashlib.sha256(config.TOKEN_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None or is_encrypted(value):
        return value
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        raise TokenKeyError("Токены в базе зашифрованы, а TOKEN_KEY не задан — верни его в переменные окружения.")
    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise TokenKeyError("TOKEN_KEY не подходит к токенам в базе — верни прежний ключ "
                            "или переподключи аккаунты (/logout, затем /start).") from None
