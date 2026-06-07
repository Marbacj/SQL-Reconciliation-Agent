"""
migrate_embeddings.py — 将 episodic_case 和 semantic_rule 表中
旧的 Bag-of-tokens 稀疏 embedding 迁移为 Dashscope dense embedding。

用法：
    python scripts/migrate_embeddings.py

环境变量：
    DASHSCOPE_API_KEY : Dashscope API Key（必填）
    SQLITE_DB_PATH    : Memory SQLite 路径（默认 data/recon_v2.sqlite）
    DRY_RUN=1         : 只打印，不写入

注意：
    - 迁移前建议备份 SQLite 文件
    - 迁移后旧的 schema_index.json 也要删除重建（启动时会自动重建）
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SQLITE_DB_PATH", "data/recon_v2.sqlite")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
BATCH_SIZE = 10  # Dashscope text-embedding-v3 单批最多 10 条
RATE_LIMIT_SLEEP = 0.5  # 每批次间隔 (s)，避免触发限速


# ── Dashscope 批量 embedding ──────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量调用 Dashscope text-embedding-v3，返回 dense float list。"""
    import dashscope
    from dashscope import TextEmbedding

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise RuntimeError("请设置环境变量 DASHSCOPE_API_KEY")

    # 过滤空文本，记录原始下标映射
    indexed = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
    if not indexed:
        return [[] for _ in texts]

    orig_indices, clean_texts = zip(*indexed)

    resp = TextEmbedding.call(
        model=TextEmbedding.Models.text_embedding_v3,
        input=list(clean_texts),
        api_key=api_key,
    )

    if not resp or not resp.output:
        raise RuntimeError(f"Dashscope 返回异常: status={getattr(resp, 'status_code', None)}, "
                           f"code={getattr(resp, 'code', None)}, message={getattr(resp, 'message', None)}")

    embeddings = resp.output["embeddings"]
    embeddings.sort(key=lambda x: x["text_index"])

    # 还原到原始 texts 顺序，空文本填 []
    result: list[list[float]] = [[] for _ in texts]
    for local_idx, emb_item in enumerate(embeddings):
        orig_idx = orig_indices[local_idx]
        result[orig_idx] = emb_item["embedding"]

    return result


# ── 主迁移逻辑 ───────────────────────────────────────────

def migrate_table(conn, table: str, text_col: str, id_col: str = "id") -> int:
    """迁移指定表的 embedding_json 字段。返回已更新行数。"""
    rows = conn.execute(
        f"SELECT {id_col}, {text_col} FROM {table} WHERE archived=0"
    ).fetchall()

    if not rows:
        logger.info("[%s] 无数据，跳过", table)
        return 0

    logger.info("[%s] 共 %d 行待迁移", table, len(rows))
    updated = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i: i + BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[1] or "" for r in batch]

        try:
            vecs = embed_batch(texts)
        except Exception as e:
            logger.error("[%s] batch %d 失败: %s，跳过", table, i, e)
            time.sleep(RATE_LIMIT_SLEEP * 2)
            continue

        if not DRY_RUN:
            for row_id, vec in zip(ids, vecs):
                conn.execute(
                    f"UPDATE {table} SET embedding_json=? WHERE {id_col}=?",
                    (json.dumps(vec), row_id),
                )
            conn.commit()

        updated += len(batch)
        logger.info("[%s] 已完成 %d / %d", table, min(i + BATCH_SIZE, len(rows)), len(rows))
        time.sleep(RATE_LIMIT_SLEEP)

    return updated


def main() -> None:
    if DRY_RUN:
        logger.info("DRY_RUN 模式：只打印不写入")

    if not os.path.exists(DB_PATH):
        logger.error("数据库文件不存在: %s", DB_PATH)
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = 0
    total += migrate_table(conn, "episodic_case", "query")
    total += migrate_table(conn, "semantic_rule", "rule_text")

    conn.close()

    # 删除旧 schema_index.json，下次启动自动重建
    schema_index_path = os.getenv("SCHEMA_INDEX_PATH", "data/schema_index.json")
    if os.path.exists(schema_index_path) and not DRY_RUN:
        os.remove(schema_index_path)
        logger.info("已删除旧 schema_index.json，下次启动将自动重建")

    logger.info("迁移完成，共更新 %d 行", total)


if __name__ == "__main__":
    main()
