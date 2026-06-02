"""OpenTelemetry Tracing 装配：可选依赖，未安装时降级为 no-op。

设计：
- `init_tracing(service_name)`：尝试装配 OTLP exporter；失败/未安装则 no-op
- `tracer`：模块级 tracer，提供 start_as_current_span 上下文管理器
- `traced(name)`：装饰器形式，自动包裹函数为 span

降级语义：
- 任何场景下，导入 `from recon_v2.infra.tracing import tracer, traced` 必须可用
- 调用 tracer.start_as_current_span() 不会抛异常
- emit span 失败时静默吞掉
"""

from __future__ import annotations

import functools
import logging
import os
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)


class _NoOpSpan:
    """No-op span 占位。"""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attrs: dict) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[dict] = None) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def set_status(self, *_args, **_kwargs) -> None:
        pass


class _NoOpTracer:
    name = "noop"

    @contextmanager
    def start_as_current_span(self, name: str, attributes: Optional[dict] = None) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()


_tracer: Any = _NoOpTracer()
_initialized = False


def init_tracing(
    service_name: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
) -> str:
    """装配 OTel，返回实际 backend 名称（"otel" / "noop"）。

    安全：任何异常都会降级为 no-op，绝不影响主流程。
    """
    global _tracer, _initialized
    if _initialized:
        return _tracer.name

    service = service_name or os.getenv("OTEL_SERVICE_NAME", "recon-v2")
    endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import (  # type: ignore
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        resource = Resource.create({"service.name": service})
        provider = TracerProvider(resource=resource)

        # OTLP exporter 是可选的（Phoenix / Jaeger 通常用这个）
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
                )
            except Exception as e:
                logger.warning("OTLP exporter init failed (%s), keep ConsoleExporter only", e)
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service)
        _tracer.name = "otel"
        _initialized = True
        return "otel"

    except Exception as e:
        logger.info("OTel not available (%s), using no-op tracer", e)
        _tracer = _NoOpTracer()
        _initialized = True
        return "noop"


def get_tracer() -> Any:
    """获取全局 tracer。首次调用时自动 init。"""
    if not _initialized:
        init_tracing()
    return _tracer


@contextmanager
def span(name: str, attributes: Optional[dict] = None) -> Iterator[Any]:
    """便捷上下文管理器。"""
    t = get_tracer()
    with t.start_as_current_span(name, attributes=attributes) as s:
        if attributes:
            try:
                s.set_attributes(attributes)
            except Exception:
                pass
        yield s


def traced(name: Optional[str] = None) -> Callable:
    """装饰器：自动给函数加 span。"""

    def deco(fn: Callable) -> Callable:
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(span_name) as s:
                try:
                    result = fn(*args, **kwargs)
                    return result
                except Exception as e:
                    try:
                        s.record_exception(e)
                    except Exception:
                        pass
                    raise

        return wrapper

    return deco
