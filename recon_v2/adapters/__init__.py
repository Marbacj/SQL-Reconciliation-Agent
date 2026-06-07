# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# adapters 包统一导出
#
# 使用示例：
#   from recon_v2.adapters import build_adapter, DataSourceConfig, DataSourceRegistry

from recon_v2.adapters.base import ExecResult, SQLAdapter
from recon_v2.adapters.sqlite_adapter import SQLiteAdapter
from recon_v2.adapters.factory import (
    DataSourceConfig,
    DataSourceEntry,
    DataSourceRegistry,
    build_adapter,
)

__all__ = [
    "ExecResult",
    "SQLAdapter",
    "SQLiteAdapter",
    "DataSourceConfig",
    "DataSourceEntry",
    "DataSourceRegistry",
    "build_adapter",
]
