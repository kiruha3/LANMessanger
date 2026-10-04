"""PSK-шифрование комнат: AES-256-GCM, ключ выводится из пароля (PBKDF2).

Ключ нигде не хранится и не передаётся — каждая сторона выводит его
из пароля комнаты. Хаб релеит шифротекст как есть и текст не читает.
"""

import base64
import datetime
import hashlib
import os
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

ITERATIONS = 100_000
NONCE_LEN = 12

CERT_FILE = "hub.crt"
KEY_FILE = "hub.key"
CERT_DAYS = 3650  # 10 лет


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


# --- TLS: self-signed сертификат узла и SSL-контексты ---

def _atomic_write(path: str, data: bytes):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def ensure_self_signed_cert(dir_path: str) -> tuple[str, str]:
    """Self-signed X.509 (CN=LANMessenger, RSA 2048, 10 лет) рядом с настройками.
    Файлы уже есть — возвращает как есть, не пересоздаёт."""
    crt_path = os.path.join(dir_path, CERT_FILE)
    key_path = os.path.join(dir_path, KEY_FILE)
    if os.path.exists(crt_path) and os.path.exists(key_path):
        return crt_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LANMessenger")])
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
            .sign(key, hashes.SHA256()))
    os.makedirs(dir_path, exist_ok=True)
    _atomic_write(key_path, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    _atomic_write(crt_path, cert.public_bytes(serialization.Encoding.PEM))
    return crt_path, key_path


def cert_fingerprint(crt_path: str) -> str:
    """SHA-256 (hex) от DER сертификата — совпадает с отпечатком,
    который видит TLS-клиент через getpeercert(binary_form=True)."""
    with open(crt_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def client_ctx() -> ssl.SSLContext:
    """Клиент: цепочку не проверяем (self-signed), доверие — через пиннинг
    отпечатка (peer_fingerprints/known_fingerprints)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def server_ctx(crt_path: str, key_path: str) -> ssl.SSLContext:
    """Сервер: клиентские сертификаты не запрашиваем."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt_path, key_path)
    return ctx
