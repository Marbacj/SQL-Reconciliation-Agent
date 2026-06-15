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


def _load_dir(directory: str, doc_prefix: str, max_chars: int = 1500) -> List[DocChunk]:
    """从目录加载所有 .md 文件，整文件作为一个 chunk（≤ max_chars），超长则按句子边界切分。"""
    import os
    chunks: List[DocChunk] = []
    if not os.path.isdir(directory):
        return chunks
    md_files = sorted(f for f in os.listdir(directory) if f.endswith(".md") and not f.startswith("."))
    if md_files:
        logger.info("chunker: loading %d docs from %s", len(md_files), directory)
    for fname in md_files:
        fpath = os.path.join(directory, fname)
        try:
            text = open(fpath, encoding="utf-8").read().strip()
            if not text or len(text) < 30:
                continue
            doc_id = f"doc:{fname.replace('.md', '')}"
            if len(text) <= max_chars:
                # 整文件一个 chunk，标题和内容不分家
                chunks.append(DocChunk(doc_id=doc_id, text=text, metadata={"type": "doc", "src": doc_id}))
            else:
                # 超长才切，按句子边界，但每块至少保留 100 字避免碎片
                sub = chunk_text_doc(doc_id, text, max_chars=max_chars)
                chunks.extend(c for c in sub if len(c.text) >= 30)
        except Exception as e:
            logger.warning("chunker: failed to load %s: %s", fpath, e)
    return chunks


def build_default_kb(kb_dir: str = "") -> List[DocChunk]:
    """默认知识库：table_docs + rules + 关联 chunk + 内置兜底规则。

    加载优先级：
    1. knowledge_base/table_docs/*.md  ← 表结构，整文件一 chunk
    2. knowledge_base/rules/*.md       ← 业务规则/PRD 提炼，整文件一 chunk
       （auto_relationships.md 作为静态快照也在此目录，但优先用动态加载覆盖）
    3. RelationshipChunkBuilder        ← 从 DB 动态生成关联 chunk（跳过 auto_relationships.md 避免重复）
    4. 内置兜底规则（仅当 rules/ 目录不存在时生效）

    kb_dir 参数只影响 table_docs 路径（向后兼容），rules 目录始终从 KB_RULES_DIR 或
    knowledge_base/rules 读取。
    """
    import os
    if not kb_dir:
        kb_dir = os.getenv("KB_DIR", "knowledge_base/table_docs")
    rules_dir = os.getenv("KB_RULES_DIR", "knowledge_base/rules")

    chunks: List[DocChunk] = []

    # ── 1. 表结构文档 ──────────────────────────────────────────────────────────
    chunks.extend(_load_dir(kb_dir, doc_prefix="doc", max_chars=1500))

    # ── 2. 业务规则文档（跳过 auto_relationships.md，由第 3 步动态生成）────────
    rel_static = os.path.join(rules_dir, "auto_relationships.md")
    rule_chunks = _load_dir(rules_dir, doc_prefix="rule", max_chars=1200)
    chunks.extend(c for c in rule_chunks if c.doc_id != "doc:auto_relationships" and not c.doc_id.startswith("doc:auto_relationships#"))

    # ── 3. 关联 chunk（从 DB 动态推断，保持代码块完整性）──────────────────────
    db_path = os.getenv("DB_PATH", "")
    if not db_path:
        for candidate in ("data/eval_data.sqlite", "data/mock_reconciliation.db", "data/unified_test.db"):
            if os.path.exists(candidate):
                db_path = candidate
                break
    if db_path:
        try:
            from recon_v2.rag.relationship_builder import RelationshipChunkBuilder
            rel_chunks = RelationshipChunkBuilder(db_path=db_path).build()
            for rc in rel_chunks:
                chunks.append(DocChunk(
                    doc_id=rc.doc_id,
                    text=rc.text,
                    metadata=rc.metadata,
                ))
            logger.info("chunker: loaded %d relation chunks from %s", len(rel_chunks), db_path)
        except Exception as e:
            logger.warning("chunker: relationship builder failed: %s", e)

    # ── 3. 内置兜底规则（rules/ 目录存在时跳过，避免与文件内容重复）──────────
    has_rules_dir = os.path.isdir(rules_dir) and any(
        f.endswith(".md") for f in os.listdir(rules_dir)
    ) if os.path.isdir(rules_dir) else False

    if not has_rules_dir:
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

    logger.info("chunker: KB built, %d chunks total", len(chunks))
    return chunks
