"""
core/encryption.py — AES-256-GCM шифрование чувствительных данных.

Используется Кроносом и оркестратором для шифрования:
  - истории диалогов
  - LLM-кэша
  - списка задач
  - секретов в sien.db

Мастер-ключ выводится из пароля через PBKDF2-HMAC-SHA256 (200 000 итераций).
Соль хранится в data/.salt (один раз, никогда не меняется).

API:
    from core.encryption import Encryptor

    enc = Encryptor.from_password("my_master_password")
    ciphertext = enc.encrypt("секретный текст")
    plaintext = enc.decrypt(ciphertext)

    # Для JSON:
    ciphertext = enc.encrypt_json({"foo": "bar"})
    obj = enc.decrypt_json(ciphertext)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("encryption")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SALT_FILE = DATA_DIR / ".salt"

PBKDF2_ITERATIONS = 200_000
KEY_LENGTH = 32      # AES-256
NONCE_LENGTH = 12    # GCM standard

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    CRYPTO_OK = True
except ImportError:
    CRYPTO_OK = False
    logger.warning("cryptography не установлена — шифрование недоступно. "
                   "Установи: pip install cryptography")


def _get_or_create_salt() -> bytes:
    """Возвращает соль, создавая при первом запуске."""
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = secrets.token_bytes(16)
    SALT_FILE.write_bytes(salt)
    try:
        os.chmod(SALT_FILE, 0o600)
    except OSError:
        pass
    return salt


class Encryptor:
    """AES-256-GCM. Один экземпляр = один ключ."""

    def __init__(self, key: bytes):
        if not CRYPTO_OK:
            raise RuntimeError("Модуль cryptography не установлен")
        if len(key) != KEY_LENGTH:
            raise ValueError(f"Ключ должен быть {KEY_LENGTH} байт")
        self._aes = AESGCM(key)

    @classmethod
    def from_password(cls, password: str, salt: Optional[bytes] = None) -> "Encryptor":
        """Создаёт Encryptor из пароля. Соль берётся из data/.salt."""
        if not CRYPTO_OK:
            raise RuntimeError("Модуль cryptography не установлен")
        salt = salt or _get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        key = kdf.derive(password.encode("utf-8"))
        return cls(key)

    # ── Базовые операции ────────────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        """Возвращает base64-строку: nonce(12) || ciphertext+tag."""
        nonce = secrets.token_bytes(NONCE_LENGTH)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token.encode("ascii"))
        nonce, ct = raw[:NONCE_LENGTH], raw[NONCE_LENGTH:]
        plaintext = self._aes.decrypt(nonce, ct, None)
        return plaintext.decode("utf-8")

    # ── JSON-обёртка ────────────────────────────────────────────

    def encrypt_json(self, obj: Any) -> str:
        return self.encrypt(json.dumps(obj, ensure_ascii=False))

    def decrypt_json(self, token: str) -> Any:
        return json.loads(self.decrypt(token))

    # ── Проверка корректности пароля ────────────────────────────

    def verify(self, token: str) -> bool:
        try:
            self.decrypt(token)
            return True
        except Exception:
            return False


# ── Вспомогательные функции для integration-слоя ────────────────

_active_encryptor: Optional[Encryptor] = None


def set_active(encryptor: Encryptor) -> None:
    """Устанавливает глобальный Encryptor (после ввода мастер-пароля)."""
    global _active_encryptor
    _active_encryptor = encryptor


def get_active() -> Optional[Encryptor]:
    return _active_encryptor


def is_unlocked() -> bool:
    return _active_encryptor is not None


def encrypt_if_possible(plaintext: str) -> str:
    """Шифрует если есть активный ключ, иначе возвращает как есть."""
    if _active_encryptor is None:
        return plaintext
    try:
        return "enc:" + _active_encryptor.encrypt(plaintext)
    except Exception as e:
        logger.error(f"encrypt_if_possible: {e}")
        return plaintext


def decrypt_if_needed(value: str) -> str:
    """Дешифрует значения с префиксом enc:, остальные возвращает как есть."""
    if not value or not value.startswith("enc:"):
        return value
    if _active_encryptor is None:
        return "[зашифровано — введите мастер-пароль]"
    try:
        return _active_encryptor.decrypt(value[4:])
    except Exception as e:
        logger.error(f"decrypt_if_needed: {e}")
        return "[ошибка дешифрования]"
