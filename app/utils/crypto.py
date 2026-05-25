"""可选密钥存储用的简易对称编码工具。"""

import base64
from app.core.config import get_settings


def encrypt_text(value: str | None) -> str | None:
    if not value:
        return None
    key = get_settings().secret_encrypt_key.encode("utf-8")
    raw = value.encode("utf-8")
    mixed = bytes([raw[i] ^ key[i % len(key)] for i in range(len(raw))])
    return base64.urlsafe_b64encode(mixed).decode("utf-8")


def decrypt_text(value: str | None) -> str | None:
    if not value:
        return None
    key = get_settings().secret_encrypt_key.encode("utf-8")
    data = base64.urlsafe_b64decode(value.encode("utf-8"))
    raw = bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    return raw.decode("utf-8")
