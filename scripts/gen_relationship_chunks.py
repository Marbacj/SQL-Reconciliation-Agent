"""自动生成关联 chunk 脚本。

用法：
  python scripts/gen_relationship_chunks.py                    # 使用默认 DB 路径
  python scripts/gen_relationship_chunks.py --db data/my.db   # 指定 DB
  python scripts/gen_relationship_chunks.py --dry-run         # 只打印，不写文件

输出：knowledge_base/rules/auto_relationships.md
（被 build_default_kb() 的 rules/ 扫描自动加载到 KB）
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from recon_v2.rag.relationship_builder import Relation, RelationshipChunkBuilder

_OUTPUT_PATH = "knowledge_base/rules/auto_relationships.md"
_HEADER = """\
<!--
此文件由 scripts/gen_relationship_chunks.py 自动生成。
如需添加自定义关联，请在运行脚本时通过 --extra 参数传入，
或在此文件末尾手动追加（重新生成时会覆盖）。
-->

"""


def resolve_db(hint: str | None) -> str:
    candidates = [
        hint,
        os.getenv("DB_PATH"),
        "data/recon.db",
        "data/sessions.sqlite",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "找不到 SQLite 数据库。请用 --db 指定路径，或设置 DB_PATH 环境变量。"
    )


def main():
    parser = argparse.ArgumentParser(description="自动生成表关联 KB chunk 文件")
    parser.add_argument("--db", default=None, help="SQLite 数据库路径（默认自动探测）")
    parser.add_argument(
        "--output", default=_OUTPUT_PATH, help=f"输出文件路径（默认 {_OUTPUT_PATH}）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    try:
        db_path = resolve_db(args.db)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"[INFO] 使用数据库: {db_path}")

    builder = RelationshipChunkBuilder(db_path=db_path)
    chunks = builder.build()

    if not chunks:
        print("[WARN] 未生成任何 chunk（数据库中未检测到表或关联）")
        sys.exit(0)

    # 合并所有 chunk 文本
    content = _HEADER + "\n\n---\n\n".join(c.text for c in chunks) + "\n"

    print(f"\n[INFO] 生成 {len(chunks)} 个 chunk（含 overview）：")
    for c in chunks:
        preview = c.text.splitlines()[0] if c.text else ""
        print(f"  - {c.doc_id}  |  {preview}")

    if args.dry_run:
        print("\n" + "=" * 60)
        print(content)
        print("=" * 60)
        print("\n[DRY RUN] 未写入文件")
        return

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n[OK] 已写入: {args.output}")
    print("重新初始化 HybridRetriever（或重启服务）后生效。")


if __name__ == "__main__":
    main()
