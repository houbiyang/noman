"""本机应用配置、连接和签名工具；业务服务不使用数据库管理账号。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from backend import database


def utcnow() -> datetime:
    """返回带时区的现实时间，不作为仿真时间。"""
    return datetime.now(timezone.utc)


def canonical(value: object) -> str:
    """本轮版本固定的规范序列化，拒绝非有限值。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value: object) -> str:
    """计算 JSON 规范摘要，文本凭据使用独立字节摘要。"""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def token_digest(value: str) -> str:
    """对高熵不透明凭据计算摘要，数据库不存原始凭据。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ServiceError(Exception):
    """带稳定代码的可公开业务错误，禁止携带数据库或凭据详情。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Settings:
    """显式依赖配置，测试可指定独立数据库及签名密钥。"""

    connection: dict
    signing_key: str
    bootstrap_key: str
    scheduler_enabled: bool = True
    demo_tick_seconds: float = 2.0

    @classmethod
    def from_local(cls) -> Settings:
        """读取本机私有配置，不在导入时创建凭据或对局。"""
        connection = json.loads(database.APPLICATION_CONFIG_PATH.read_text(encoding="utf-8"))
        runtime = json.loads((database.LOCAL_ROOT / "backend-runtime-v1.json").read_text(encoding="utf-8"))
        bootstrap = (database.LOCAL_ROOT / "bootstrap-key").read_text(encoding="utf-8-sig").strip()
        return cls(connection, runtime["signing_key"], bootstrap)

    def connect(self) -> psycopg.Connection:
        """创建短事务连接；退出上下文时提交或回滚，并关闭连接。"""
        config = {key: self.connection[key] for key in ("host", "port", "user", "password")}
        config.update(dbname=self.connection["database"], connect_timeout=5, row_factory=dict_row,
                      options="-c search_path=sandbox,pg_catalog -c timezone=UTC -c statement_timeout=15000 -c lock_timeout=5000")
        return psycopg.connect(**config)

    def sign(self, payload: dict) -> str:
        """签名版本化内部上下文，不把秘密放进令牌载荷。"""
        encoded = base64.urlsafe_b64encode(canonical(payload).encode("utf-8")).decode().rstrip("=")
        signature = hmac.new(self.signing_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    def verify(self, value: str, kind: str) -> dict:
        """验证签名、用途与现实有效期；授权仍单独校验会话。"""
        encoded, separator, signature = value.partition(".")
        expected = hmac.new(self.signing_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not separator or not hmac.compare_digest(expected, signature):
            raise ServiceError(409, "CONTEXT_INVALID", "上下文或预览签名无效，请重新读取")
        try:
            result = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (ValueError, UnicodeError) as error:
            raise ServiceError(409, "CONTEXT_INVALID", "上下文格式无效") from error
        if result.get("kind") != kind or result.get("expires_at", 0) < utcnow().timestamp():
            raise ServiceError(409, "CONTEXT_EXPIRED", "上下文或预览已过期，请重新读取")
        return result


def initialize_runtime() -> None:
    """首次启动时显式准备私有签名配置，不覆盖旧配置或签发业务会话。"""
    target = database.LOCAL_ROOT / "backend-runtime-v1.json"
    if not target.exists():
        database.write_private_json(target, dict(schema_version="backend-runtime-v1", signing_key=secrets.token_hex(32)))
