"""
Агент 'Кронос' — Хранитель секретов.
- AES-256 (GCM) шифрование → secrets.json.aes
- Мастер-пароль через консоль при первом запуске
- HTTP /secret — выдаёт секрет в память на 5 минут
- Белый список агентов
"""
import asyncio
import base64
import getpass
import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger("cronos")
logging.basicConfig(level=logging.INFO)

SECRETS_FILE = "secrets.json.aes"
SALT = b"sien01-cronos-salt-v1"
TTL = 300  # 5 минут в секундах

# Белый список: имя агента → Bearer-токен (простейшая аутентификация)
ALLOWED_AGENTS: dict[str, str] = {
    "orchestrator": "token-orchestrator-alpha",
    "argus": "token-argus-alpha",
    # Добавляй агентов сюда
}


def derive_key(password: str) -> bytes:
    """PBKDF2-HMAC-SHA256 → 256-bit ключ для AES-GCM."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        SALT,
        iterations=200_000,
        dklen=32,
    )


def encrypt_secrets(data: dict, key: bytes) -> bytes:
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    plaintext = json.dumps(data).encode()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # Формат хранения: nonce(12) || ciphertext
    return base64.b64encode(nonce + ciphertext)


def decrypt_secrets(raw: bytes, key: bytes) -> dict:
    decoded = base64.b64decode(raw)
    nonce, ciphertext = decoded[:12], decoded[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)


class SecretCache:
    """In-memory кэш с TTL."""

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    def set(self, key: str, value: Any):
        self._store[key] = (value, time.time() + TTL)

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.time() > expires:
            del self._store[key]
            return None
        return value

    def invalidate(self, key: str):
        self._store.pop(key, None)

    async def evict_loop(self):
        """Фоновая задача: чистит просроченные записи."""
        while True:
            now = time.time()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            await asyncio.sleep(60)


class CronosAgent:
    def __init__(self):
        self._key: Optional[bytes] = None
        self._secrets: dict = {}
        self._cache = SecretCache()
        self._app = FastAPI(title="Кронос", version="0.1.0-alpha")
        self._setup_routes()

    # ── Инициализация ──────────────────────────────────────────────────────────

    def _load_or_create_secrets(self):
        if os.path.exists(SECRETS_FILE):
            logger.info("Загружаю существующее хранилище секретов...")
            password = getpass.getpass("Мастер-пароль Кроноса: ")
            self._key = derive_key(password)
            with open(SECRETS_FILE, "rb") as f:
                raw = f.read()
            try:
                self._secrets = decrypt_secrets(raw, self._key)
                logger.info("Хранилище расшифровано успешно.")
            except Exception:
                raise RuntimeError("Неверный мастер-пароль или повреждённый файл.")
        else:
            logger.info("Первый запуск — создаю новое хранилище.")
            password = getpass.getpass("Придумай мастер-пароль: ")
            confirm = getpass.getpass("Подтверди мастер-пароль: ")
            if password != confirm:
                raise ValueError("Пароли не совпадают.")
            self._key = derive_key(password)
            # Базовые секреты-заглушки
            self._secrets = {
                "db_password": "change_me",
                "telegram_token": "YOUR_BOT_TOKEN",
                "api_key_example": "sk-example",
            }
            self._save_secrets()
            logger.info(f"Хранилище создано: {SECRETS_FILE}")

    def _save_secrets(self):
        encrypted = encrypt_secrets(self._secrets, self._key)
        with open(SECRETS_FILE, "wb") as f:
            f.write(encrypted)

    # ── FastAPI роуты ──────────────────────────────────────────────────────────

    def _setup_routes(self):

        @self._app.get("/health")
        async def health():
            return {"status": "ok", "agent": "cronos"}

        @self._app.get("/secret/{key_name}")
        async def get_secret(
            key_name: str,
            x_agent_name: str = Header(...),
            x_agent_token: str = Header(...),
        ):
            # Проверка белого списка
            expected_token = ALLOWED_AGENTS.get(x_agent_name)
            if expected_token is None or expected_token != x_agent_token:
                raise HTTPException(status_code=403, detail="Доступ запрещён")

            # Сначала смотрим в кэш
            cached = self._cache.get(key_name)
            if cached is not None:
                return {"key": key_name, "value": cached, "cached": True}

            # Берём из хранилища
            value = self._secrets.get(key_name)
            if value is None:
                raise HTTPException(status_code=404, detail="Секрет не найден")

            # Кладём в кэш на 5 минут
            self._cache.set(key_name, value)
            return {"key": key_name, "value": value, "cached": False}

        @self._app.post("/secret/{key_name}")
        async def set_secret(
            key_name: str,
            body: dict,
            x_agent_name: str = Header(...),
            x_agent_token: str = Header(...),
        ):
            """Только оркестратор может писать секреты."""
            if x_agent_name != "orchestrator" or ALLOWED_AGENTS.get("orchestrator") != x_agent_token:
                raise HTTPException(status_code=403, detail="Только оркестратор может писать")
            value = body.get("value")
            if value is None:
                raise HTTPException(status_code=400, detail="Поле 'value' обязательно")
            self._secrets[key_name] = value
            self._save_secrets()
            self._cache.invalidate(key_name)
            return {"status": "saved", "key": key_name}

    # ── Жизненный цикл ────────────────────────────────────────────────────────

    async def run(self):
        # Инициализация хранилища (блокирующий getpass — нужно запускать до asyncio loop)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_or_create_secrets)

        asyncio.create_task(self._cache.evict_loop())

        config = uvicorn.Config(self._app, host="0.0.0.0", port=8001, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()


if __name__ == "__main__":
    agent = CronosAgent()
    asyncio.run(agent.run())
