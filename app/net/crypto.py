"""PSK-шифрование комнат: AES-256-GCM, ключ выводится из пароля (PBKDF2).

Ключ нигде не хранится и не передаётся — каждая сторона выводит его
из пароля комнаты. Хаб релеит шифротекст как есть и текст не читает.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 100_000
NONCE_LEN = 12


def room_key(password: str, room: str) -> bytes:
    """Ключ комнаты из пароля: PBKDF2-HMAC-SHA256, соль = имя комнаты."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), room.encode("utf-8"),
        ITERATIONS, dklen=32,
    )


def encrypt(key: bytes, data: bytes) -> str:
    """AES-GCM: base64(nonce12 + ciphertext с тегом)."""
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(key: bytes, payload_b64: str) -> bytes | None:
    """None при любой ошибке (чужой ключ, битый пакет) — исключений наружу."""
    try:
        raw = base64.b64decode(payload_b64, validate=True)
        if len(raw) <= NONCE_LEN:
            return None
        return AESGCM(key).decrypt(raw[:NONCE_LEN], raw[NONCE_LEN:], None)
    except Exception:
        return None


def is_encrypted(pkt: dict) -> bool:
    return pkt.get("enc") is True
