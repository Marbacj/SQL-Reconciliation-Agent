"""
认证模块 — JWT Token 认证 + 用户管理 + 租户模型配置。

表结构：
  users               — 用户/租户账户（username 即 tenant_id）
  tenant_model_config — 租户级别 LLM 配置（可覆盖全局 env）

默认初始化 test 用户（用户名: test，密码: test123，角色: admin）。

@author mabohui <mabohui@kuaishou.com>
Created on 2026-06-04
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

# ── 可选依赖（优雅降级） ─────────────────────────────────────────────────────
try:
    from jose import JWTError, jwt as _jose_jwt          # type: ignore
    _HAS_JOSE = True
except ImportError:
    _jose_jwt = None
    _HAS_JOSE = False

try:
    from passlib.context import CryptContext              # type: ignore
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    _HAS_PASSLIB = True
except ImportError:
    _pwd_ctx = None
    _HAS_PASSLIB = False

try:
    from fastapi import Depends, HTTPException, status    # type: ignore
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    _security = HTTPBearer(auto_error=False)
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

# ── 配置 ────────────────────────────────────────────────────────────────────
_AUTH_DB   = os.getenv("AUTH_DB_PATH", "data/auth.sqlite")
JWT_SECRET = os.getenv("JWT_SECRET", "recon-agent-secret-change-in-prod")
JWT_ALGO   = "HS256"
JWT_EXPIRE = int(os.getenv("JWT_EXPIRE_HOURS", "24")) * 3600  # seconds

# 初始 test 用户（可通过环境变量覆盖）
_INIT_USER = os.getenv("INIT_USERNAME", "test")
_INIT_PWD  = os.getenv("INIT_PASSWORD", "test123")
_INIT_ROLE = os.getenv("INIT_ROLE", "admin")


# ── 数据类 ───────────────────────────────────────────────────────────────────
@dataclass
class TenantInfo:
    tenant_id: str
    username: str
    role: str


@dataclass
class TenantModelConfig:
    tenant_id: str
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0


# ── 密码工具 ─────────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    if _HAS_PASSLIB:
        return _pwd_ctx.hash(plain)
    # 降级：简单 SHA256（生产环境必须安装 passlib）
    import hashlib
    return "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    if _HAS_PASSLIB and not hashed.startswith("sha256:"):
        return _pwd_ctx.verify(plain, hashed)
    import hashlib
    return hashed == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


# ── JWT ──────────────────────────────────────────────────────────────────────
def create_token(tenant_id: str, role: str) -> str:
    payload = {
        "sub": tenant_id,
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRE,
    }
    if _HAS_JOSE:
        return _jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    # 降级：base64 编码（不安全，仅开发用）
    import base64, json
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_token(token: str) -> Optional[dict]:
    if _HAS_JOSE:
        try:
            return _jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        except JWTError:
            return None
    # 降级解码
    import base64, json
    try:
        payload = json.loads(base64.urlsafe_b64decode(token + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ── 数据库 ───────────────────────────────────────────────────────────────────
def _ensure_auth_db():
    os.makedirs(os.path.dirname(_AUTH_DB) if os.path.dirname(_AUTH_DB) else ".", exist_ok=True)
    conn = sqlite3.connect(_AUTH_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL UNIQUE,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            created       INTEGER NOT NULL,
            updated       INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenant_model_config (
            tenant_id   TEXT PRIMARY KEY,
            provider    TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            api_key     TEXT NOT NULL DEFAULT '',
            base_url    TEXT NOT NULL DEFAULT '',
            temperature REAL NOT NULL DEFAULT 0.0,
            updated     INTEGER NOT NULL DEFAULT 0
        )
    """)
    # 初始化 test 用户
    now = int(time.time() * 1000)
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, username, password_hash, role, created, updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, _INIT_USER, _INIT_USER, hash_password(_INIT_PWD), _INIT_ROLE, now, now),
    )
    conn.commit()
    conn.close()


@contextmanager
def _auth_conn():
    conn = sqlite3.connect(_AUTH_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_ensure_auth_db()


# ── 用户 CRUD ────────────────────────────────────────────────────────────────
def get_user_by_username(username: str) -> Optional[dict]:
    with _auth_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
    return dict(row) if row else None


def create_user(username: str, password: str, role: str = "user") -> dict:
    now = int(time.time() * 1000)
    uid = uuid.uuid4().hex
    with _auth_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, username, password_hash, role, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, username, username, hash_password(password), role, now, now),
        )
    return {"id": uid, "tenant_id": username, "username": username, "role": role}


# ── 租户模型配置 ─────────────────────────────────────────────────────────────
def get_tenant_model_config(tenant_id: str) -> TenantModelConfig:
    with _auth_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_model_config WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
    if row:
        return TenantModelConfig(
            tenant_id=row["tenant_id"],
            provider=row["provider"],
            model=row["model"],
            api_key=row["api_key"],
            base_url=row["base_url"],
            temperature=row["temperature"],
        )
    return TenantModelConfig(tenant_id=tenant_id)


def upsert_tenant_model_config(tenant_id: str, data: dict) -> TenantModelConfig:
    now = int(time.time() * 1000)
    with _auth_conn() as conn:
        conn.execute("""
            INSERT INTO tenant_model_config (tenant_id, provider, model, api_key, base_url, temperature, updated)
            VALUES (:tenant_id, :provider, :model, :api_key, :base_url, :temperature, :updated)
            ON CONFLICT(tenant_id) DO UPDATE SET
              provider=excluded.provider, model=excluded.model,
              api_key=excluded.api_key, base_url=excluded.base_url,
              temperature=excluded.temperature, updated=excluded.updated
        """, {
            "tenant_id": tenant_id,
            "provider": data.get("provider", ""),
            "model": data.get("model", ""),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "temperature": float(data.get("temperature", 0.0)),
            "updated": now,
        })
    return get_tenant_model_config(tenant_id)


# ── FastAPI Depends ──────────────────────────────────────────────────────────
def get_current_tenant():
    """FastAPI Depends — 从 Bearer token 中提取当前租户信息。"""
    if not _HAS_FASTAPI:
        raise RuntimeError("FastAPI not installed")

    def _inner(cred: Optional[HTTPAuthorizationCredentials] = Depends(_security)):
        if cred is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录，请先获取 Token")
        payload = decode_token(cred.credentials)
        if payload is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")
        user = get_user_by_username(payload["sub"])
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
        return TenantInfo(
            tenant_id=user["tenant_id"],
            username=user["username"],
            role=user["role"],
        )
    return _inner
