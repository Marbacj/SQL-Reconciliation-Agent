"""Eval Runner：在测试库上跑 Golden Set，输出 4 维 metric + Markdown 报告。

用法：
    # 跑 v2（默认 stub adapter）
    python -m tests.eval.runner --target v2 --db data/eval_data.sqlite

    # 跑 v1 baseline（接入 legacy hello_agents.ReconciliationAgent）
    python -m tests.eval.runner --target v1 --db data/eval_data.sqlite

    # 对比
    python -m tests.eval.runner --target v2 --compare v1 --db data/eval_data.sqlite

Adapter 协议（v1/v2 各实现）：
    class TargetAdapter:
        def solve(self, query: str) -> SolveResult: ...
            返回 (sql, answer, latency_ms, token_cost, cost_usd)

为了在 Stage 0 立刻跑通 baseline，本文件内置：
- StubAdapter：直接返回 expected_sql（验证评测脚本本身正确性，应得 ~100%）
- V1Adapter：基于现有 v1 ReconciliationAgent（若可 import）
- V2Adapter：Stage 2 完成后由 recon_v2.orchestration.graph 提供
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

# 让 `python -m tests.eval.runner` 在项目根目录工作
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.metrics import CaseMetric, aggregate, exec_accuracy, semantic_match
from tests.eval.schema import GoldenCase, load_golden_set, stats


@dataclass
class SolveResult:
    sql: str
    answer: str
    latency_ms: float = 0.0
    token_cost: int = 0
    cost_usd: float = 0.0
    error: str = ""


class TargetAdapter(Protocol):
    name: str

    def solve(self, query: str, db_path: str) -> SolveResult:  # pragma: no cover
        ...


# ---------- Stub：让评测脚本自检（应得 100% exec_accuracy）----------


class StubAdapter:
    name = "stub"

    def __init__(self, golden: List[GoldenCase]):
        self._by_query = {c.query: c for c in golden}

    def solve(self, query: str, db_path: str) -> SolveResult:
        case = self._by_query.get(query)
        if case is None:
            return SolveResult(sql="", answer="", error="case not found")
        # 透传 expected_sql；对 REJECT/CLARIFY 也透传
        return SolveResult(
            sql=case.expected_sql,
            answer=case.expected_result_summary,
            latency_ms=1.0,
            token_cost=0,
            cost_usd=0.0,
        )


# ---------- V1：legacy hello_agents 适配 ----------


class V1Adapter:
    name = "v1"

    def __init__(self):
        # 延迟 import，避免 v1 缺依赖时直接挂掉
        try:
            from hello_agents.agents.reconciliation_agent import ReconciliationAgent  # type: ignore

            self._agent_cls = ReconciliationAgent
            self._agent = None
            self._available = True
            self._error = ""
        except Exception as e:
            self._agent_cls = None
            self._agent = None
            self._available = False
            self._error = f"v1 not importable: {e}"

    def solve(self, query: str, db_path: str) -> SolveResult:
        if not self._available:
            return SolveResult(sql="", answer="", error=self._error)
        # v1 agent 缺少 LLM key 时也会失败 — 这是预期的，会被记录到 reason
        try:
            if self._agent is None:
                # 这里只是最简实例化，v1 真实跑通需要 LLM 配置
                self._agent = self._agent_cls()  # type: ignore
            t0 = time.time()
            res = self._agent.run(query)  # 假设 v1 有 run 接口
            latency = (time.time() - t0) * 1000
            sql = getattr(res, "sql", "") or ""
            answer = getattr(res, "answer", str(res))
            return SolveResult(sql=sql, answer=answer, latency_ms=latency)
        except Exception as e:
            return SolveResult(sql="", answer="", error=f"v1 runtime error: {e}")


# ---------- V2：占位，待 Stage 2 graph 接入 ----------


class V2Adapter:
    name = "v2"

    def __init__(self):
        try:
            from recon_v2.orchestration.graph import build_graph  # type: ignore

            self._available = True
            self._build_graph = build_graph
        except Exception as e:
            self._available = False
            self._error = f"v2 graph not ready: {e}"

    def solve(self, query: str, db_path: str) -> SolveResult:
        if not self._available:
            return SolveResult(sql="", answer="", error=self._error)
        try:
            # 用 run_once 便捷入口避开手动 ctx 装配
            from recon_v2.orchestration.graph import run_once  # type: ignore

            t0 = time.time()
            out = run_once(query, db_path)
            latency = (time.time() - t0) * 1000
            return SolveResult(
                sql=out.get("sql", ""),
                answer=out.get("answer", ""),
                latency_ms=latency,
                token_cost=out.get("token_cost", 0),
                cost_usd=out.get("cost_usd", 0.0),
            )
        except Exception as e:
            return SolveResult(sql="", answer="", error=f"v2 runtime error: {e}")


# ---------- Runner 核心 ----------


def _make_adapter(target: str, golden: List[GoldenCase]) -> TargetAdapter:
    if target == "stub":
        return StubAdapter(golden)
    if target == "v1":
        return V1Adapter()
    if target == "v2":
        return V2Adapter()
    raise ValueError(f"Unknown target: {target}")


def run_single(adapter: TargetAdapter, case: GoldenCase, db_path: str) -> CaseMetric:
    t0 = time.time()
    res = adapter.solve(case.query, db_path)
    latency = res.latency_ms or (time.time() - t0) * 1000

    if res.error:
        return CaseMetric(
            case_id=case.id,
            exec_acc=0,
            sem_match=0,
            latency_ms=latency,
            token_cost=res.token_cost,
            cost_usd=res.cost_usd,
            error=res.error,
            reason="adapter error",
        )

    acc, reason = exec_accuracy(db_path, res.sql, case.expected_sql)
    sem, sem_reason = semantic_match(res.answer, case.expected_result_summary)

    return CaseMetric(
        case_id=case.id,
        exec_acc=acc,
        sem_match=sem,
        latency_ms=latency,
        token_cost=res.token_cost,
        cost_usd=res.cost_usd,
        error="",
        reason=f"{reason} | {sem_reason}",
    )


def run(
    target: str,
    db_path: str,
    golden_path: str,
    report_dir: str,
    limit: Optional[int] = None,
) -> dict:
    cases = load_golden_set(golden_path)
    if limit:
        cases = cases[:limit]

    adapter = _make_adapter(target, cases)
    print(f"[runner] target={target} adapter={adapter.name} cases={len(cases)}")

    metrics: List[CaseMetric] = []
    for i, case in enumerate(cases, 1):
        m = run_single(adapter, case, db_path)
        metrics.append(m)
        flag = "OK" if m.exec_acc and m.sem_match else ("ACC" if m.exec_acc else "FAIL")
        print(f"  [{i:>2}/{len(cases)}] {case.id:<8} {flag:<4} {m.reason[:60]}")

    agg = aggregate(metrics)
    coverage = stats(cases)

    Path(report_dir).mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = Path(report_dir) / f"{target}_{ts}.md"
    _write_report(report_path, target, agg, coverage, metrics)

    print(f"\n[runner] Report -> {report_path}")
    print(json.dumps(agg, indent=2))
    return {"agg": agg, "metrics": [dataclasses.asdict(m) for m in metrics], "report": str(report_path)}


def _write_report(path: Path, target: str, agg: dict, coverage: dict, metrics: List[CaseMetric]):
    lines: List[str] = []
    lines.append(f"# Eval Report - target: {target}")
    lines.append(f"\n_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n")

    lines.append("## Coverage\n")
    lines.append(f"- Total: **{coverage['total']}**")
    lines.append(f"- By intent: `{coverage['by_intent']}`")
    lines.append(f"- By difficulty: `{coverage['by_difficulty']}`\n")

    lines.append("## Aggregated Metrics\n")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Exec-Accuracy | **{agg['exec_accuracy']:.2%}** |")
    lines.append(f"| Semantic-Match | **{agg['semantic_match']:.2%}** |")
    lines.append(f"| Avg Latency (ms) | {agg['avg_latency_ms']:.1f} |")
    lines.append(f"| P95 Latency (ms) | {agg['p95_latency_ms']:.1f} |")
    lines.append(f"| Total tokens | {agg['total_tokens']} |")
    lines.append(f"| Total cost (USD) | {agg['total_cost_usd']:.4f} |\n")

    lines.append("## Per-case Detail\n")
    lines.append("| case_id | exec | sem | latency_ms | reason |")
    lines.append("| --- | --- | --- | --- | --- |")
    for m in metrics:
        lines.append(
            f"| {m.case_id} | {m.exec_acc} | {m.sem_match} | {m.latency_ms:.1f} | "
            f"{(m.error or m.reason)[:80]} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["stub", "v1", "v2"], default="stub")
    parser.add_argument("--compare", choices=["stub", "v1", "v2"], default=None)
    parser.add_argument("--db", default="data/eval_data.sqlite")
    parser.add_argument("--golden", default="tests/eval/golden_set.jsonl")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条用于 smoke test")
    args = parser.parse_args()

    main_res = run(args.target, args.db, args.golden, args.report_dir, args.limit)

    if args.compare:
        cmp_res = run(args.compare, args.db, args.golden, args.report_dir, args.limit)
        # 差异表
        a = main_res["agg"]
        b = cmp_res["agg"]
        print("\n=== Comparison ===")
        print(f"  Exec-Accuracy   {args.target}={a['exec_accuracy']:.2%}  vs  {args.compare}={b['exec_accuracy']:.2%}  Δ={a['exec_accuracy']-b['exec_accuracy']:+.2%}")
        print(f"  Semantic-Match  {args.target}={a['semantic_match']:.2%}  vs  {args.compare}={b['semantic_match']:.2%}  Δ={a['semantic_match']-b['semantic_match']:+.2%}")
        print(f"  Avg Latency     {args.target}={a['avg_latency_ms']:.1f}ms  vs  {args.compare}={b['avg_latency_ms']:.1f}ms")


if __name__ == "__main__":
    main()
