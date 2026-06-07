# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# DataSourceRegistry + build_adapter 工厂
#
# 使用方式：
#   from recon_v2.adapters.factory import DataSourceRegistry, DataSourceConfig, build_adapter
#
#   # 注册一个 MySQL 数据源
#   registry = DataSourceRegistry.get_instance()
#   registry.register("prod_mysql", DataSourceConfig(
#       type="mysql", host="localhost", port=3306,
#       user="root", password="xxx", database="orders"
#   ))
#
#   # 获取 adapter
#   adapter = registry.get_adapter("prod_mysql")

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from recon_v2.adapters.base import SQLAdapter

logger = logging.getLogger(__name__)

# 持久化路径
_DEFAULT_REGISTRY_PATH = os.getenv("DATASOURCES_DB_PATH", "data/datasources.json")


# ── 数据源配置模型 ────────────────────────────────────────────

class DataSourceConfig(BaseModel):
    """统一数据源配置。type 决定走哪个 Adapter。"""
    type: str = Field(..., description="数据源类型: sqlite | mysql | postgres")

    # SQLite 专用
    db_path: Optional[str] = Field(None, description="SQLite 文件路径")

    # MySQL / PostgreSQL 通用
    host: Optional[str] = Field(None, description="数据库主机")
    port: Optional[int] = Field(None, description="端口")
    user: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码（存储时会原样保存，生产环境建议接 Secret 管理）")
    database: Optional[str] = Field(None, description="数据库名")

    # PostgreSQL 专用
    pg_schema: Optional[str] = Field("public", description="PostgreSQL schema，默认 public")

    # 通用选项
    timeout: float = Field(10.0, description="查询超时秒数")
    charset: Optional[str] = Field("utf8mb4", description="字符集（MySQL 专用）")

    # 元信息
    description: Optional[str] = Field(None, description="数据源描述，方便识别用途")


class DataSourceEntry(BaseModel):
    """Registry 中存储的条目（配置 + 元信息）。"""
    name: str
    config: DataSourceConfig
    enabled: bool = True


# ── 工厂函数 ─────────────────────────────────────────────────

def build_adapter(cfg: DataSourceConfig) -> SQLAdapter:
    """根据 DataSourceConfig.type 构建对应的 SQLAdapter 实例。

    Raises:
        ValueError: 不支持的 type
        ImportError: 对应数据库驱动未安装
    """
    t = cfg.type.lower().strip()

    if t == "sqlite":
        if not cfg.db_path:
            raise ValueError("SQLite 数据源必须提供 db_path")
        from recon_v2.adapters.sqlite_adapter import SQLiteAdapter
        return SQLiteAdapter(db_path=cfg.db_path, timeout=cfg.timeout)

    elif t == "mysql":
        from recon_v2.adapters.mysql_adapter import MySQLAdapter
        return MySQLAdapter(
            host=cfg.host or "127.0.0.1",
            port=cfg.port or 3306,
            user=cfg.user or "root",
            password=cfg.password or "",
            database=cfg.database or "",
            charset=cfg.charset or "utf8mb4",
            timeout=cfg.timeout,
        )

    elif t in ("postgres", "postgresql", "pg"):
        from recon_v2.adapters.postgres_adapter import PostgreSQLAdapter
        return PostgreSQLAdapter(
            host=cfg.host or "127.0.0.1",
            port=cfg.port or 5432,
            user=cfg.user or "postgres",
            password=cfg.password or "",
            database=cfg.database or "postgres",
            schema=cfg.pg_schema or "public",
            timeout=cfg.timeout,
        )

    else:
        raise ValueError(
            f"不支持的数据源类型: '{t}'。"
            f"支持: sqlite | mysql | postgres"
        )


# ── DataSourceRegistry ────────────────────────────────────────

class DataSourceRegistry:
    """全局数据源注册表（线程安全单例）。

    - 配置持久化到 JSON 文件（data/datasources.json）
    - 线程安全：所有写操作加锁
    - adapter 懒加载：get_adapter() 时才真正建连
    """

    _instance: Optional[DataSourceRegistry] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self, registry_path: str = _DEFAULT_REGISTRY_PATH):
        self._path = Path(registry_path)
        self._entries: Dict[str, DataSourceEntry] = {}
        self._write_lock = threading.Lock()
        self._load()

    @classmethod
    def get_instance(cls, registry_path: str = _DEFAULT_REGISTRY_PATH) -> "DataSourceRegistry":
        """获取全局单例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(registry_path)
        return cls._instance

    # ── 持久化 ────────────────────────────────────────────────

    def _load(self) -> None:
        """从 JSON 文件加载配置。文件不存在时静默跳过。"""
        if not self._path.exists():
            logger.debug("DataSourceRegistry: 配置文件不存在，从空白启动 %s", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for name, entry_dict in raw.items():
                entry = DataSourceEntry.model_validate(entry_dict)
                self._entries[name] = entry
            logger.info("DataSourceRegistry: 加载 %d 个数据源", len(self._entries))
        except Exception as e:
            logger.error("DataSourceRegistry: 加载失败 %s — %s", self._path, e)

    def _save(self) -> None:
        """将当前注册表持久化到 JSON 文件。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {name: entry.model_dump() for name, entry in self._entries.items()}
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("DataSourceRegistry: 持久化失败 — %s", e)

    # ── CRUD ──────────────────────────────────────────────────

    def register(self, name: str, cfg: DataSourceConfig, description: str = "") -> None:
        """注册或更新一个数据源。"""
        if description and not cfg.description:
            cfg = cfg.model_copy(update={"description": description})
        with self._write_lock:
            self._entries[name] = DataSourceEntry(name=name, config=cfg)
            self._save()
        logger.info("DataSourceRegistry: 注册数据源 '%s' (type=%s)", name, cfg.type)

    def unregister(self, name: str) -> bool:
        """删除数据源，返回是否成功找到并删除。"""
        with self._write_lock:
            if name not in self._entries:
                return False
            del self._entries[name]
            self._save()
        logger.info("DataSourceRegistry: 删除数据源 '%s'", name)
        return True

    def get_config(self, name: str) -> Optional[DataSourceConfig]:
        """获取数据源配置（不建连）。"""
        entry = self._entries.get(name)
        return entry.config if entry else None

    def get_adapter(self, name: str) -> SQLAdapter:
        """获取数据源的 Adapter 实例（懒加载，每次调用新建实例）。

        Raises:
            KeyError: 数据源不存在
            ValueError/ImportError: adapter 构建失败
        """
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"数据源 '{name}' 未注册，请先调用 registry.register()")
        if not entry.enabled:
            raise ValueError(f"数据源 '{name}' 已禁用")
        return build_adapter(entry.config)

    def list_all(self) -> List[Dict]:
        """列出所有数据源（密码字段脱敏）。"""
        result = []
        for name, entry in self._entries.items():
            cfg_dict = entry.config.model_dump()
            if cfg_dict.get("password"):
                cfg_dict["password"] = "***"  # 脱敏
            result.append({
                "name": name,
                "type": entry.config.type,
                "enabled": entry.enabled,
                "description": entry.config.description or "",
                "config": cfg_dict,
            })
        return result

    def ping(self, name: str) -> bool:
        """测试指定数据源连通性。"""
        try:
            adapter = self.get_adapter(name)
            if hasattr(adapter, "ping"):
                return adapter.ping()
            # SQLiteAdapter 没有 ping，直接 explain 一条简单 SQL
            result = adapter.explain("SELECT 1")
            return result.success
        except Exception as e:
            logger.warning("DataSourceRegistry: ping '%s' 失败 — %s", name, e)
            return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用数据源。"""
        with self._write_lock:
            if name not in self._entries:
                return False
            self._entries[name] = self._entries[name].model_copy(update={"enabled": enabled})
            self._save()
        return True
