from context_usage import Message, estimate_message_tokens, estimate_text_tokens


def test_estimate_text_tokens_uses_language_aware_heuristic() -> None:
    assert estimate_text_tokens("abcdefgh") == 2
    assert estimate_text_tokens("你好世界") == 4


def test_estimate_message_tokens_includes_small_overhead() -> None:
    estimate = estimate_message_tokens(Message(role="user", content="abcdefgh"))
    assert estimate >= 8
