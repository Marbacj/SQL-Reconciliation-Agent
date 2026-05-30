"""
意图注册表 — 管理所有 Intent，提供分类能力

分类策略：关键词快速匹配 → LLM 兜底 → 低置信度反问
"""

import json
import re
from typing import List, Optional, Tuple

from .intent import Intent, IntentLabel, ALL_INTENTS, DEFAULT_INTENT


class IntentRegistry:
    """意图注册表：存储 Intent + 分类用户查询"""

    def __init__(self, intents: Optional[List[Intent]] = None):
        self._intents: dict[str, Intent] = {}
        for intent in (intents or ALL_INTENTS):
            self.register(intent)

    def register(self, intent: Intent):
        """注册一个意图"""
        self._intents[intent.name] = intent

    def get(self, name: str) -> Optional[Intent]:
        return self._intents.get(name)

    def list_all(self) -> List[Intent]:
        return list(self._intents.values())

    # ── 分类 ──

    def classify(
        self,
        query: str,
        llm=None,  # 可选的 LLM 实例（用于 LLM 兜底分类）
    ) -> IntentLabel:
        """分类用户查询

        优先级：关键词匹配 → LLM 分类 → 反问

        Args:
            query: 用户输入
            llm: LLM 实例（可选，用于 LLM 兜底分类）

        Returns:
            IntentLabel 包含 intent_name、confidence、method
        """
        # 1. 关键词快速匹配
        label = self._keyword_match(query)
        if label:
            return label

        # 2. LLM 兜底分类
        if llm:
            label = self._llm_classify(query, llm)
            if label and label.confidence >= 0.6:
                return label

        # 3. 无法确定 → 反问
        return IntentLabel(
            intent="clarify",
            confidence=0.0,
            method="fallback",
            reasoning="无关键词匹配且无 LLM 或 LLM 置信度不足",
        )

    # ── 关键词匹配 ──

    def _keyword_match(self, query: str) -> Optional[IntentLabel]:
        """基于关键词的快速匹配（零 LLM 调用）"""
        query_lower = query.lower()
        scores: List[Tuple[float, Intent, str]] = []

        for intent in self._intents.values():
            if not intent.keywords:
                continue
            matched = []
            for kw in intent.keywords:
                if kw.lower() in query_lower:
                    matched.append(kw)
            if matched:
                # 匹配数 / 总关键词数 = 原始分数
                raw_score = len(matched) / len(intent.keywords)
                # 匹配到的关键词长度加权（长关键词更精准）
                length_bonus = sum(len(kw) for kw in matched) / 100
                score = raw_score + length_bonus
                scores.append((score, intent, ", ".join(matched)))

        if not scores:
            return None

        scores.sort(reverse=True)
        best_score, best_intent, matched_kws = scores[0]

        # 只有得分 > 阈值才返回（避免误匹配）
        if best_score > 0.1:
            confidence = min(best_score * 2, 1.0)  # 缩放到 0-1
            return IntentLabel(
                intent=best_intent.name,
                confidence=round(confidence, 2),
                method="keyword",
                reasoning=f"匹配关键词: {matched_kws}",
            )

        return None

    # ── LLM 分类 ──

    def _llm_classify(self, query: str, llm) -> Optional[IntentLabel]:
        """LLM 兜底分类（一次轻量调用，不用 tools）"""
        intent_list = "\n".join(
            f"- {i.name}: {i.description}"
            for i in self._intents.values()
            if i.name != "clarify"
        )

        prompt = f"""你是一个意图分类器。根据用户输入，判断属于哪种意图。

可选意图：
{intent_list}

分类规则：
- 用户明确要求"对账/对比/找差异" → reconciliation
- 用户问"查询/统计/汇总/有多少" → adhoc_query
- 用户问"表结构/字段/schema" → schema_lookup
- 都不匹配 → clarify

用户输入："{query}"

请返回 JSON 格式：
{{"intent": "意图名", "confidence": 0.0-1.0, "reasoning": "一句话理由"}}

只返回 JSON，不要其他内容。"""

        try:
            response = llm.invoke(prompt)
            # 从响应中提取 JSON
            text = response.content if hasattr(response, 'content') else str(response)
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                data = json.loads(json_match.group())
                return IntentLabel(
                    intent=data.get("intent", "clarify"),
                    confidence=float(data.get("confidence", 0.5)),
                    method="llm",
                    reasoning=data.get("reasoning", ""),
                )
        except Exception:
            pass

        return None

    # ── LLM 分类的 Prompt（供外部使用） ──

    def build_classifier_prompt(self, query: str) -> str:
        """构建分类 Prompt（用于观察/调试）"""
        intent_list = "\n".join(
            f"- {i.name}: {i.description}"
            for i in self._intents.values()
            if i.name != "clarify"
        )
        return f"""意图分类请求：
意图列表：{intent_list}
用户输入："{query}"
请返回 JSON：{{"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}}"""
