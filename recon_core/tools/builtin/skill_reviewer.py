"""
Skill Reviewer — 对话经验异步提炼后写入 Skill 库

设计：
  1. Agent 执行完成后，回调 SkillReviewer.review()
  2. 提取成功模式（SQL 模板、工作流步骤、差异判断规则）
  3. 写入 Skill 库（JSON 文件），标注来源会话
  4. 下次会话时，IntentRouter 加载匹配的 Skill 作为 few-shot 参考

异步：review() 方法设计为非阻塞，可以在后台线程运行
"""

import json
import os
import re
import threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Skill:
    """可复用技能条目"""
    id: str
    name: str
    description: str
    category: str  # "sql_pattern" | "workflow" | "rule" | "term_mapping"
    content: Dict[str, Any]  # 技能内容（SQL、规则等）
    source_session: str = ""  # 来源会话 ID
    usage_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_used: Optional[str] = None


class SkillReviewer:
    """技能审查器 — 从对话中提炼可复用技能"""

    def __init__(self, skill_dir: str = "skill_library"):
        self.skill_dir = skill_dir
        os.makedirs(skill_dir, exist_ok=True)
        self._index_path = os.path.join(skill_dir, "_index.json")
        self._skills: Dict[str, Skill] = self._load()
        self._lock = threading.RLock()  # 可重入：_save_skill 内部会调 _persist

    # ── 审查入口 ──

    def review(
        self,
        query: str,
        execution_trace: str,
        final_result: str,
        intent: str = "reconciliation",
        async_mode: bool = True,
    ):
        """审查一次对话，提炼技能

        Args:
            query: 用户查询
            execution_trace: 执行轨迹（Agent 的 stdout 输出）
            final_result: 最终结果
            intent: 意图类型
            async_mode: 是否异步执行（默认 True，不阻塞主流程）
        """
        if async_mode:
            t = threading.Thread(
                target=self._review_impl,
                args=(query, execution_trace, final_result, intent),
                daemon=True,
            )
            t.start()
        else:
            self._review_impl(query, execution_trace, final_result, intent)

    def _review_impl(self, query: str, trace: str, result: str, intent: str):
        """审查实现（在后台线程运行）"""
        try:
            # 1. 提取 SQL 模板
            self._extract_sql_patterns(query, trace, result)

            # 2. 提取差异判断规则
            self._extract_diff_rules(query, result)

            # 3. 提取术语映射
            self._extract_term_mappings(trace)

            print(f"📝 Skill Reviewer: 审查完成 [{intent}]")
        except Exception as e:
            print(f"⚠️ Skill Reviewer 审查异常: {e}")

    # ── SQL 模式提取 ──

    def _extract_sql_patterns(self, query: str, trace: str, result: str):
        """从执行轨迹中提取成功的 SQL 模式"""
        # 匹配 sql_execute 或 sql_validate 中的 SQL
        sqls = re.findall(
            r'(?:sql_execute|sql_validate).*?[\"\\\']sql[\"\\\']\s*:\s*[\"\\\'](.+?)[\"\\\']',
            trace, re.DOTALL | re.IGNORECASE
        )

        # 也匹配裸 SQL（SELECT...FROM...）
        bare_sqls = re.findall(
            r'(SELECT\s+.+?\s+FROM\s+\w+.+?)(?:\n|ORDER BY|GROUP BY|LIMIT|$)',
            trace, re.IGNORECASE | re.DOTALL
        )

        all_sqls = sqls + bare_sqls
        seen = set()

        for sql in all_sqls[:5]:  # 最多保存 5 条
            sql_clean = sql.strip().replace('\\n', ' ').replace('\\"', '"')
            if sql_clean in seen or len(sql_clean) < 10:
                continue
            seen.add(sql_clean)

            # 提取关键词作为技能名
            tables = re.findall(r'FROM\s+(\w+)', sql_clean, re.IGNORECASE)
            table_tag = "_".join(tables[:2]) if tables else "query"

            skill = Skill(
                id=f"sql_{table_tag}_{len(seen)}",
                name=f"SQL 模式: {table_tag}",
                description=query[:80],
                category="sql_pattern",
                content={
                    "sql": sql_clean,
                    "tables": tables,
                    "query_example": query,
                },
            )
            self._save_skill(skill)

    # ── 差异规则提取 ──

    def _extract_diff_rules(self, query: str, result: str):
        """从结果中提取差异判断规则"""
        # 提取差异百分比阈值
        thresholds = re.findall(
            r'差异[在超]?\s*(\d+)[%％]',
            result
        )

        if thresholds or "差异" in result:
            skill = Skill(
                id=f"rule_diff_{datetime.now().strftime('%H%M%S')}",
                name="差异判断规则",
                description="对账差异的判断标准和阈值",
                category="rule",
                content={
                    "query_context": query[:100],
                    "has_diff": "差异" in result and "无差异" not in result,
                    "threshold_percent": int(thresholds[0]) if thresholds else None,
                    "result_summary": result[:300],
                },
            )
            self._save_skill(skill)

    # ── 术语映射提取 ──

    def _extract_term_mappings(self, trace: str):
        """从 SQL 中提取字段 → 表映射"""
        # 匹配 table.column 或 column 出现在 schema 输出中
        mappings = re.findall(
            r'(\w+)\s*[⟷↔→]\s*(\w+)',
            trace
        )

        for left, right in mappings[:10]:
            skill = Skill(
                id=f"term_{left}_{right}",
                name=f"术语映射: {left} ↔ {right}",
                description=f"跨表列名对应: {left} ⟷ {right}",
                category="term_mapping",
                content={
                    "left_column": left,
                    "right_column": right,
                    "context": "跨表对账字段映射",
                },
            )
            self._save_skill(skill)

    # ── Skill 库管理 ──

    def _save_skill(self, skill: Skill):
        """保存技能到库"""
        with self._lock:
            # 去重：同名且同 category 的 skill 更新而非新增
            for existing_id, existing in list(self._skills.items()):
                if existing.name == skill.name and existing.category == skill.category:
                    existing.usage_count += 1
                    existing.last_used = datetime.now().isoformat()
                    existing.content = skill.content  # 更新内容
                    self._persist()
                    return

            self._skills[skill.id] = skill
            self._persist()

    def find_skills(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Skill]:
        """检索匹配的技能"""
        query_lower = query.lower()
        scored = []

        for skill in self._skills.values():
            if category and skill.category != category:
                continue

            # 关键词匹配
            content_str = json.dumps(skill.content, ensure_ascii=False).lower()
            desc_lower = skill.description.lower()
            score = 0
            for word in query_lower.split():
                if word in desc_lower:
                    score += 2
                if word in content_str:
                    score += 1

            # 使用频率加权
            score += skill.usage_count * 0.5

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def stats(self) -> dict:
        return {
            "total_skills": len(self._skills),
            "by_category": self._count_by_category(),
        }

    def _count_by_category(self) -> Dict[str, int]:
        from collections import Counter
        return dict(Counter(s.category for s in self._skills.values()))

    # ── 持久化 ──

    def _load(self) -> Dict[str, Skill]:
        if os.path.exists(self._index_path):
            data = json.loads(open(self._index_path).read())
            return {k: Skill(**v) for k, v in data.items()}
        return {}

    def _persist(self):
        with self._lock:
            with open(self._index_path, 'w') as f:
                json.dump(
                    {k: v.__dict__ for k, v in self._skills.items()},
                    f, ensure_ascii=False, indent=2
                )
