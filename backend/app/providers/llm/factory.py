from app.config import get_settings

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        s = get_settings()
        if s.llm_provider == "anthropic":
            from app.providers.llm.anthropic_llm import AnthropicLLM

            _llm = AnthropicLLM(s.anthropic_api_key, s.classify_model, s.summary_model)
        else:
            from app.providers.llm.fake import FakeLLM

            _llm = FakeLLM()
    return _llm


def set_llm(llm) -> None:
    """Test hook."""
    global _llm
    _llm = llm
