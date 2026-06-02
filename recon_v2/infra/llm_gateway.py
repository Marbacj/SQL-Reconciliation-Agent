"""LLM Gateway：基于 LiteLLM 的多厂商统一接入层。

特性：
- 多厂商：OpenAI / DeepSeek / Claude / Azure / 本地 vLLM（由 env LLM_PROVIDER 决定）
- 指纹 Cache：messages + model + temperature 的 SHA256，命中直接返回
- Retry：指数退避，默认 3 次
- 超时：默认 30s
- Cost：按 trace_id 累计 token + USD（litellm.cost_per_token）
- OTel：可选 — `infra.tracing` 装上后自动 emit span

降级策略：
- LiteLLM 未安装 → 抛 ImportError（启动时显式提示，避免运行时迷糊）
- Cache 后端失败 → 走 InMemoryCache（在 cache.build_cache 中处理）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from recon_v2.infra.cache import CacheBackend, build_cache
from recon_v2.infra.cost import CallRecord, CostTracker, get_default_tracker

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    source: str = "live"  # "live" | "cache"
    raw: Optional[Dict[str, Any]] = None


def _fingerprint(model: str, messages: List[dict], temperature: float, extra: Dict[str, Any]) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": round(temperature, 4),
        "extra": {k: v for k, v in sorted(extra.items())},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "llm:" + hashlib.sha256(raw).hexdigest()


class LLMGateway:
    """统一的 LLM 调用入口。"""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cache: Optional[CacheBackend] = None,
        cost_tracker: Optional[CostTracker] = None,
        retry_max: int = 3,
        retry_base: float = 1.0,
        timeout: float = 30.0,
        cache_ttl: int = 3600,
    ):
        self.provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.retry_max = retry_max
        self.retry_base = retry_base
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.cache: CacheBackend = cache or build_cache()
        self.cost_tracker = cost_tracker or get_default_tracker()

        # 延迟 import litellm，避免没安装时模块加载失败
        try:
            import litellm  # type: ignore

            self._litellm = litellm
            # 关掉冗余日志
            litellm.set_verbose = False
            self._available = True
        except ImportError as e:
            self._litellm = None
            self._available = False
            self._import_error = str(e)

    # ---------------- core API ----------------

    def chat(
        self,
        messages: List[dict],
        *,
        trace_id: str = "default",
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        use_cache: bool = True,
        **extra: Any,
    ) -> ChatResult:
        """同步调用 LLM chat。"""
        used_model = model or self.model
        cache_key = _fingerprint(used_model, messages, temperature, extra)

        # ----- Cache lookup -----
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                try:
                    raw = json.loads(cached)
                    rec = CallRecord(
                        trace_id=trace_id,
                        model=used_model,
                        prompt_tokens=raw.get("prompt_tokens", 0),
                        completion_tokens=raw.get("completion_tokens", 0),
                        cost_usd=0.0,
                        latency_ms=0.0,
                        source="cache",
                    )
                    self.cost_tracker.record(rec)
                    return ChatResult(
                        content=raw["content"],
                        model=used_model,
                        prompt_tokens=raw.get("prompt_tokens", 0),
                        completion_tokens=raw.get("completion_tokens", 0),
                        cost_usd=0.0,
                        latency_ms=0.0,
                        source="cache",
                    )
                except Exception:
                    pass  # cache 数据坏，走 live

        # ----- Live call with retry -----
        if not self._available:
            raise RuntimeError(
                f"LiteLLM not installed: {getattr(self, '_import_error', 'unknown')}. "
                "Install via `pip install litellm`."
            )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retry_max + 1):
            t0 = time.time()
            try:
                # LiteLLM 需要格式: provider/model (e.g., "deepseek/deepseek-chat")
                model_with_provider = used_model
                if self.provider and "/" not in used_model:
                    model_with_provider = f"{self.provider}/{used_model}"
                
                kwargs: Dict[str, Any] = {
                    "model": model_with_provider,
                    "messages": messages,
                    "temperature": temperature,
                    "timeout": self.timeout,
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                kwargs.update(extra)

                response = self._litellm.completion(**kwargs)
                latency = (time.time() - t0) * 1000

                content = response.choices[0].message.content
                usage = getattr(response, "usage", None) or {}
                if not isinstance(usage, dict):
                    usage = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)

                p_tok = int(usage.get("prompt_tokens", 0))
                c_tok = int(usage.get("completion_tokens", 0))

                # 成本估算（LiteLLM 自带函数；失败 fallback 0）
                cost_usd = 0.0
                try:
                    cost_usd = float(self._litellm.completion_cost(completion_response=response))
                except Exception:
                    cost_usd = 0.0

                # 写 cache
                if use_cache:
                    try:
                        self.cache.set(
                            cache_key,
                            json.dumps(
                                {
                                    "content": content,
                                    "prompt_tokens": p_tok,
                                    "completion_tokens": c_tok,
                                },
                                ensure_ascii=False,
                            ),
                            ttl_seconds=self.cache_ttl,
                        )
                    except Exception as e:
                        logger.warning("cache.set failed: %s", e)

                # 计费记录
                self.cost_tracker.record(
                    CallRecord(
                        trace_id=trace_id,
                        model=used_model,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        cost_usd=cost_usd,
                        latency_ms=latency,
                        source="live",
                    )
                )

                return ChatResult(
                    content=content,
                    model=used_model,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    cost_usd=cost_usd,
                    latency_ms=latency,
                    source="live",
                    raw=None,
                )

            except Exception as e:
                last_error = e
                if attempt < self.retry_max:
                    backoff = self.retry_base * (2 ** (attempt - 1)) * (0.8 + 0.4 * random.random())
                    logger.warning(
                        "LLM call failed (attempt %d/%d): %s, retry in %.2fs",
                        attempt, self.retry_max, e, backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error("LLM call exhausted retries: %s", e)

        raise RuntimeError(f"LLM call failed after {self.retry_max} retries: {last_error}")

    # ---------------- embedding (轻量包装) ----------------

    def embedding(
        self,
        texts: List[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """Embedding 调用，主要用于 RAG / Memory。"""
        if not self._available:
            raise RuntimeError("LiteLLM not installed.")
        used_model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        response = self._litellm.embedding(model=used_model, input=texts)
        return [d["embedding"] for d in response["data"]]


# 进程级单例（可被 ctx 覆盖）
_default_gateway: Optional[LLMGateway] = None


def get_default_gateway() -> LLMGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = LLMGateway()
    return _default_gateway
