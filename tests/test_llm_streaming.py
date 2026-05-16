"""LLM streaming response compatibility tests."""

import asyncio
from types import SimpleNamespace

from hello_agents.core.llm_adapters import OpenAIAdapter


def _content_chunk(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
        usage=None,
    )


def _usage_chunk():
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
        ),
    )


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks

    def create(self, **kwargs):
        return iter(self.chunks)


class FakeChat:
    def __init__(self, chunks):
        self.completions = FakeCompletions(chunks)


class FakeClient:
    def __init__(self, chunks):
        self.chat = FakeChat(chunks)


class FakeAsyncStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        self._iterator = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeAsyncCompletions:
    def __init__(self, chunks):
        self.chunks = chunks

    async def create(self, **kwargs):
        return FakeAsyncStream(self.chunks)


class FakeAsyncChat:
    def __init__(self, chunks):
        self.completions = FakeAsyncCompletions(chunks)


class FakeAsyncClient:
    def __init__(self, chunks):
        self.chat = FakeAsyncChat(chunks)


def test_openai_stream_skips_empty_or_missing_choices():
    adapter = OpenAIAdapter("test-key", "https://api.test/v1", 60, "test-model")
    adapter._client = FakeClient(
        [
            SimpleNamespace(choices=[], usage=None),
            SimpleNamespace(usage=None),
            _content_chunk("hello"),
            _usage_chunk(),
        ]
    )

    chunks = list(adapter.stream_invoke([{"role": "user", "content": "hi"}]))

    assert chunks == ["hello"]
    assert adapter.last_stats.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }


def test_openai_astream_skips_empty_or_missing_choices():
    async def collect_chunks(adapter):
        return [
            chunk
            async for chunk in adapter.astream_invoke(
                [{"role": "user", "content": "hi"}]
            )
        ]

    adapter = OpenAIAdapter("test-key", "https://api.test/v1", 60, "test-model")
    adapter._async_client = FakeAsyncClient(
        [
            SimpleNamespace(choices=[], usage=None),
            SimpleNamespace(usage=None),
            _content_chunk("hello"),
            _usage_chunk(),
        ]
    )

    chunks = asyncio.run(collect_chunks(adapter))

    assert chunks == ["hello"]
    assert adapter.last_stats.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
