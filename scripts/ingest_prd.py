"""PRD 投喂工具：把 PRD 文档提炼成 KB rules/*.md 文件。

用法：
  python scripts/ingest_prd.py prd.md --name gmv_calculation
  python scripts/ingest_prd.py prd.txt --name refund_policy --dry-run

原理：
  把 PRD 全文发给 LLM，要求只提取对 SQL 生成有用的部分，
  输出标准化的 Markdown（字段定义 / 计算口径 / 异常规则 / 常见查询）。
  人工确认后写入 knowledge_base/rules/<name>.md。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_EXTRACT_SYSTEM = """\
You are a knowledge engineer preparing documents for a SQL data agent.

Given a Product Requirements Document (PRD), extract ONLY the information useful for SQL query generation:
1. Table/field definitions and their business meaning
2. Metric calculation formulas (e.g., GMV = ...)
3. Business rules and thresholds (e.g., refund within 7 days)
4. Data relationships between tables
5. Status values and their meanings
6. Commonly used filters or grouping dimensions

Output as clean Markdown with these sections (omit sections with no content):
# <Topic Name>

## 字段/指标定义
(table with 指标 | 计算公式 | 数据来源)

## 业务规则
(bullet list of rules with exact thresholds)

## 术语映射
(table with 用户说 | 实际含义 | SQL字段)

## 常见查询示例
(SQL code blocks)

IMPORTANT:
- Remove all UI descriptions, acceptance criteria, mockup references
- Keep only what affects SQL generation
- If a rule has an exact number, preserve it
- Output in Chinese
"""


def extract_from_prd(prd_text: str, llm) -> str:
    result = llm.chat(
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": f"PRD Content:\n\n{prd_text}"},
        ],
        temperature=0.0,
        max_tokens=2000,
        use_cache=False,
    )
    return result.content.strip()


def main():
    parser = argparse.ArgumentParser(description="PRD → KB rules 提炼工具")
    parser.add_argument("prd_file", help="PRD 文件路径（.md 或 .txt）")
    parser.add_argument("--name", required=True, help="输出文件名（不含 .md），如 gmv_calculation")
    parser.add_argument("--output-dir", default="knowledge_base/rules", help="输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印提炼结果，不写文件")
    args = parser.parse_args()

    # 读 PRD
    with open(args.prd_file, encoding="utf-8") as f:
        prd_text = f.read()
    print(f"[INFO] PRD 长度: {len(prd_text)} 字符")

    # 初始化 LLM
    from recon_v2.infra.llm_gateway import LLMGateway
    llm = LLMGateway()
    if not llm._available:
        print("[ERROR] LLM 未配置（缺少 LLM_API_KEY），无法提炼")
        sys.exit(1)

    # 提炼
    print("[INFO] 正在提炼（调用 LLM）...")
    extracted = extract_from_prd(prd_text, llm)

    print("\n" + "=" * 60)
    print("提炼结果：")
    print("=" * 60)
    print(extracted)
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] 未写入文件")
        return

    # 写文件
    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, f"{args.name}.md")
    if os.path.exists(out_path):
        answer = input(f"\n[WARN] {out_path} 已存在，覆盖？(y/N): ").strip().lower()
        if answer != "y":
            print("已取消")
            return

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(extracted)
    print(f"\n[OK] 已写入: {out_path}")
    print("重启 KB 后生效（重新初始化 HybridRetriever）。")


if __name__ == "__main__":
    main()
