#!/usr/bin/env python3
"""SQL Reconciliation Agent v2 - CLI Demo。

用法：
    python apps/cli/demo.py --query "查询今天订单总额"
    python apps/cli/demo.py  # 进入交互 REPL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import warnings

warnings.filterwarnings("ignore")  # 过滤 LangGraph beta 警告


def _run(query: str, db: str) -> None:
    from recon_v2.orchestration.graph import run_once

    out = run_once(query, db)
    print(f"\n🎯 Intent : {out.get('intent')} (conf={out.get('confidence', 0):.2f})")
    print(f"📊 Status : {out.get('final_status')}")
    sql = out.get("sql", "")
    if sql and sql not in {"REJECT", "CLARIFY"}:
        print(f"🛠  SQL    : {sql[:120]}")
    answer = out.get("answer", "")
    print(f"💬 Answer : {answer[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Reconciliation Agent v2 CLI")
    parser.add_argument("--query", "-q", default=None, help="single query mode")
    parser.add_argument("--db", default="data/eval_data.sqlite", help="SQLite DB path")
    args = parser.parse_args()

    if args.query:
        _run(args.query, args.db)
        return

    # REPL
    print("SQL Reconciliation Agent v2 — REPL (Ctrl+C to exit)")
    print(f"Using DB: {args.db}\n")
    while True:
        try:
            q = input("Query> ").strip()
            if not q:
                continue
            if q.lower() in {"exit", "quit"}:
                break
            _run(q, args.db)
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break


if __name__ == "__main__":
    main()
