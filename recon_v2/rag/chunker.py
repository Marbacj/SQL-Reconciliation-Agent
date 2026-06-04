"""RAG document chunker：把表 schema / 列描述 / 业务文档切块。

设计：
- DocChunk 是统一的检索单元
- chunk_table_schema: 一张表 → 1 chunk（含列定义 + 业务说明）
- chunk_text_doc: 长文本按句子切分，512 tokens 上限（简易按字符近似）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class DocChunk:
    doc_id: str
    text: str
    metadata: Dict[str, str] = field(default_factory=dict)


def chunk_table_schema(
    table_name: str,
    columns: List[Dict[str, str]],
    description: str = "",
) -> DocChunk:
    """把单张表的 schema 转成一个 chunk。

    columns: [{"name": "id", "type": "TEXT", "comment": "订单 ID"}, ...]
    """
    col_lines = [f"  - {c['name']} ({c.get('type', '')}): {c.get('comment', '')}" for c in columns]
    text = (
        f"Table: {table_name}\n"
        f"Description: {description}\n"
        f"Columns:\n" + "\n".join(col_lines)
    )
    return DocChunk(
        doc_id=f"table:{table_name}",
        text=text,
        metadata={"type": "schema", "table": table_name},
    )


def chunk_text_doc(doc_id: str, text: str, max_chars: int = 800) -> List[DocChunk]:
    """长文本按句子边界切块。"""
    sentences = re.split(r"(?<=[。！？\.\!\?])\s+", text.strip())
    chunks: List[DocChunk] = []
    buf: List[str] = []
    cur_len = 0
    seq = 0
    for sent in sentences:
        if cur_len + len(sent) > max_chars and buf:
            chunks.append(
                DocChunk(
                    doc_id=f"{doc_id}#{seq}",
                    text="".join(buf),
                    metadata={"type": "doc", "src": doc_id},
                )
            )
            seq += 1
            buf = []
            cur_len = 0
        buf.append(sent)
        cur_len += len(sent)
    if buf:
        chunks.append(
            DocChunk(
                doc_id=f"{doc_id}#{seq}",
                text="".join(buf),
                metadata={"type": "doc", "src": doc_id},
            )
        )
    return chunks


def build_default_kb(kb_dir: str = "") -> List[DocChunk]:
    """v2 默认知识库：静态业务规则 + 磁盘文档自动加载。

    加载优先级：
    1. 硬编码的核心业务规则（对账规则/方言/术语）——始终存在
    2. 磁盘上的 knowledge_base/table_docs/*.md 文件——自动读取切块

    kb_dir: 知识库文档目录，默认从 KB_DIR 环境变量或 knowledge_base/table_docs 读取
    """
    import os
    if not kb_dir:
        kb_dir = os.getenv("KB_DIR", "knowledge_base/table_docs")

    chunks: List[DocChunk] = []

    # ── 对账业务规则（阈值 + 异常定义）──
    chunks.extend(
        chunk_text_doc(
            "doc:reconciliation_rules",
            "对账规则与异常判断标准："
            "1) 金额不一致：|orders.amount - payments.amount| > 0.01 视为对账差异，容差 0.01 元以内忽略。"
            "2) 漏支付：orders.status='paid' 但在 payments 中无 status='success' 记录，属高危异常。"
            "3) 孤儿退款：refunds.order_id 在 orders.id 中不存在，属脏数据，高危。"
            "4) 重复支付：同一 order_id 有多条 payments.status='success'，属异常。"
            "5) 负金额订单：orders.amount < 0 属异常数据，需单独统计。"
            "6) 时间窗口对账：默认按 created_at 字段，按天聚合（DATE(created_at) 分组）。"
            "7) 净收入计算：净收入 = SUM(paid 订单金额) - SUM(refunds 退款金额)，不含 pending/cancelled。",
        )
    )

    # ── SQLite 方言提示（静态知识，LLM 无法从 schema 推断）──
    chunks.extend(
        chunk_text_doc(
            "doc:sqlite_dialect",
            "SQLite 日期函数（禁止使用 MySQL/PG 方言）："
            "当天：DATE('now')；昨天：DATE('now', '-1 day')；"
            "7 天前：DATE('now', '-7 days')；上月：DATE('now', '-1 month')；"
            "年月维度：strftime('%Y-%m', created_at)；时分秒：strftime('%H:%M:%S', created_at)。"
            "禁止使用：INTERVAL、CURDATE()、NOW()、DATE_SUB()、EXTRACT()、DATEDIFF()。"
            "标准差替代：SQRT(AVG((val-(SELECT AVG(val) FROM t))*(val-(SELECT AVG(val) FROM t))))。",
        )
    )

    # ── 业务术语映射 ──
    chunks.extend(
        chunk_text_doc(
            "doc:business_terms",
            "业务术语映射："
            "支付成功率 = COUNT(status='success') / COUNT(*) FROM payments，按 order_id 关联；"
            "订单完成率 = COUNT(status='paid') / COUNT(*) FROM orders；"
            "退款率 = COUNT(DISTINCT refunds.order_id) / COUNT(DISTINCT orders.id WHERE status='paid')；"
            "平均订单金额 = AVG(amount) FROM orders WHERE status='paid'（排除 pending/cancelled）；"
            "对账差异金额 = ABS(o.amount - p.amount) WHERE 差值 > 0.01。",
        )
    )

    # ── 磁盘知识库文档自动加载 ──
    if os.path.isdir(kb_dir):
        md_files = sorted(
            f for f in os.listdir(kb_dir)
            if f.endswith(".md") and not f.startswith(".")
        )
        if md_files:
            logger.info("chunker: loading %d docs from %s", len(md_files), kb_dir)
        for fname in md_files:
            fpath = os.path.join(kb_dir, fname)
            try:
                text = open(fpath, encoding="utf-8").read()
                doc_id = f"doc:{fname.replace('.md', '')}"
                file_chunks = chunk_text_doc(doc_id, text, max_chars=800)
                chunks.extend(file_chunks)
            except Exception as e:
                logger.warning("chunker: failed to load %s: %s", fpath, e)
    else:
        logger.debug("chunker: kb_dir %s not found, skip disk docs", kb_dir)

    return chunks
