import json

import anthropic

from src.pipeline.dev_llm_stub import CANNED_NARRATIVE, CANNED_SENTIMENT, StubAnthropicClient


def test_create_returns_shape_generate_narrative_expects():
    """generate_narrative() indexes response.content[0] and requires a TextBlock instance."""
    client = StubAnthropicClient()
    response = client.messages.create(model="claude-stub", max_tokens=4096, messages=[])

    assert isinstance(response.content[0], anthropic.types.TextBlock)
    payload = json.loads(response.content[0].text)
    assert payload["narrative"] == CANNED_NARRATIVE
    assert payload["sentiment"] == CANNED_SENTIMENT

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.cache_read_input_tokens == 0


def test_canned_narrative_is_visibly_marked():
    assert "STUB-LLM" in CANNED_NARRATIVE
