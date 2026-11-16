"""Token and cost tracking for evals. No network, no API key."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

from evals import usage as usage_module
from evals.usage import PRICES, RecordingClient, Usage, format_cost
from src.api.chat import stream_chat


class _FakeStream:
    def __init__(self, text, content, stop_reason, usage):
        self._events = [SimpleNamespace(type="text", text=t) for t in text]
        self._final = SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


def _usage(input_tokens, output_tokens, **cache):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, **cache)


def _fake_client(turns):
    queue = list(turns)
    return SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: queue.pop(0)))


def _two_round_turns():
    tool = ToolUseBlock(type="tool_use", id="t1", name="get_stock_snapshot", input={"symbol": "NVDA"})
    return [
        _FakeStream([], [tool], "tool_use", _usage(100, 20)),
        _FakeStream(["Done."], [TextBlock(type="text", text="Done.")], "end_turn", _usage(250, 40)),
    ]


def _run(client):
    tool_runner = lambda name, inp: (json.dumps({"close": 1}), False)  # noqa: E731
    return list(stream_chat([{"role": "user", "content": "hi"}], client=client, model="m", tool_runner=tool_runner))


def test_usage_is_summed_across_every_api_call_in_a_tool_loop():
    recorder = RecordingClient(_fake_client(_two_round_turns()))
    _run(recorder)
    assert recorder.usage.calls == 2
    assert recorder.usage.input_tokens == 350
    assert recorder.usage.output_tokens == 60


def test_recording_does_not_change_what_the_chat_loop_yields():
    plain = _run(_fake_client(_two_round_turns()))
    recorded = _run(RecordingClient(_fake_client(_two_round_turns())))
    assert recorded == plain


def test_cost_is_exact_for_priced_models_and_none_for_unknown_ones():
    one_million_each = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert one_million_each.cost("claude-haiku-5-5") == pytest.approx(0.10 + 0.50)
    assert one_million_each.cost("claude-sonnet-5-5") == pytest.approx(2.00 + 10.00)
    assert one_million_each.cost("some-future-model") is None


def test_every_price_is_input_cheaper_than_output():
    # A swapped pair would silently misreport cost: output tokens are always the pricier ones.
    for model, (price_in, price_out) in PRICES.items():
        assert 0 < price_in < price_out, model


def test_cache_tokens_are_counted_separately_and_none_fields_are_zero():
    total = Usage()
    total.record(_usage(10, 5, cache_creation_input_tokens=7, cache_read_input_tokens=3))
    total.record(_usage(10, 5, cache_creation_input_tokens=None, cache_read_input_tokens=None))
    total.record(_usage(10, 5))  # no cache fields at all
    assert (total.input_tokens, total.output_tokens, total.cache_tokens) == (30, 15, 10)


def test_usage_add_combines_runs():
    a, b = Usage(calls=1, input_tokens=10, output_tokens=2), Usage(calls=2, input_tokens=5, output_tokens=1)
    a.add(b)
    assert a.to_dict() == {"calls": 3, "input_tokens": 15, "output_tokens": 3, "cache_tokens": 0}


def test_format_cost_shows_dollars_per_run_or_says_the_price_is_missing():
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    line = format_cost(usage, "claude-sonnet-5-5", runs=4)
    assert "$2.0000 total" in line
    assert "$0.5000 per run" in line
    assert usage_module.PRICES_AS_OF in line
    assert "cost unknown" in format_cost(usage, "some-future-model", runs=4)
