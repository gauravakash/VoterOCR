"""LLM provider factory + token-usage callback.

Hybrid routing config (Flash primary + Pro fallback) is managed here. Other
providers (Claude, GPT-4) still work for the single-model path via
`get_vision_llm()`. Sarvam Document Intelligence is a separate job-based
OCR API and is NOT exposed through this factory.

`TokenUsageCallback` is the source of truth for cost: it captures the
actual prompt/candidate token counts from each Gemini response so cost is
*measured*, not estimated. Attach via `chain.ainvoke(input, config={"callbacks":[cb]})`.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

from .. import config


class TokenUsageCallback(BaseCallbackHandler):
    """Captures actual token usage from a single LLM call.

    LangChain's google-genai integration surfaces Gemini's `usage_metadata`
    in several places depending on version. We probe all the spots and take
    whichever is populated. Counts are additive across multiple calls on
    the same callback instance (don't reuse across pages).
    """

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cached_tokens: int = 0
        self.calls: int = 0

    def _consume_usage(self, usage: Any) -> None:
        if not usage:
            return
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        if not isinstance(usage, dict):
            return
        self.input_tokens += int(
            usage.get("input_tokens")
            or usage.get("prompt_token_count")
            or usage.get("prompt_tokens")
            or 0
        )
        self.output_tokens += int(
            usage.get("output_tokens")
            or usage.get("candidates_token_count")
            or usage.get("completion_tokens")
            or 0
        )
        self.cached_tokens += int(
            usage.get("cached_content_token_count")
            or usage.get("cache_read_input_tokens")
            or 0
        )

    def _absorb_response(self, response) -> None:
        # 1) llm_output dict (some adapters surface tokens here).
        try:
            llm_out = getattr(response, "llm_output", None) or {}
            self._consume_usage(llm_out.get("usage_metadata"))
            self._consume_usage(llm_out.get("token_usage"))
            self._consume_usage(llm_out)  # in case tokens are at top level
        except Exception:
            pass
        # 2) generations[].generation_info / message.usage_metadata
        try:
            for gen_list in getattr(response, "generations", []):
                for gen in gen_list:
                    info = getattr(gen, "generation_info", None) or {}
                    self._consume_usage(info.get("usage_metadata"))
                    self._consume_usage(info.get("token_usage"))
                    msg = getattr(gen, "message", None)
                    if msg is None:
                        continue
                    self._consume_usage(getattr(msg, "usage_metadata", None))
                    rmd = getattr(msg, "response_metadata", None) or {}
                    if isinstance(rmd, dict):
                        self._consume_usage(rmd.get("usage_metadata"))
                        self._consume_usage(rmd.get("token_usage"))
                        # Some Gemini adapters nest under a 'usage' key.
                        self._consume_usage(rmd.get("usage"))
                    # AIMessageChunk variants
                    self._consume_usage(getattr(msg, "additional_kwargs", {}).get("usage_metadata"))
        except Exception:
            pass

    # Chat models fire on_llm_end in newer LangChain, but some versions use
    # on_chat_model_end — implement both so we don't miss tokens either way.
    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        self.calls += 1
        self._absorb_response(response)

    def on_chat_model_end(self, response, **kwargs) -> None:  # type: ignore[attr-defined]
        self.calls += 1
        self._absorb_response(response)


def current_provider() -> str:
    return config.LLM_PROVIDER


def current_vision_model() -> str:
    """Backwards-compatible single-model name (used by output metadata)."""
    return config.VISION_MODEL


def primary_vision_model_name() -> str:
    return config.PRIMARY_VISION_MODEL


def fallback_vision_model_name() -> str:
    return config.FALLBACK_VISION_MODEL


def _gemini(model: str, *, temperature: float = 0.0, max_tokens: int = 8192):
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )


def get_vision_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
):
    """Single-model getter — used when hybrid routing is not desired."""
    provider = (provider or config.LLM_PROVIDER).lower()
    model = model or config.VISION_MODEL

    if provider == "gemini":
        return _gemini(model, temperature=temperature, max_tokens=max_tokens)

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model if "claude" in (model or "") else "claude-sonnet-4-5-20250929",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if provider == "gpt4":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model if model and "gpt" in model else "gpt-4o",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    raise ValueError(
        f"Unsupported chat-LLM provider: {provider!r}. "
        f"Sarvam Document Intelligence runs through the legacy job pipeline."
    )


def get_primary_vision_llm():
    """Cheap/fast model used for 95%+ of pages."""
    if config.LLM_PROVIDER == "gemini":
        return _gemini(config.PRIMARY_VISION_MODEL)
    return get_vision_llm(model=config.PRIMARY_VISION_MODEL)


def get_fallback_vision_llm():
    """Expensive but more accurate model — only invoked when primary fails validation."""
    if config.LLM_PROVIDER == "gemini":
        return _gemini(config.FALLBACK_VISION_MODEL)
    return get_vision_llm(model=config.FALLBACK_VISION_MODEL)


def get_text_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
):
    return get_vision_llm(
        provider=provider,
        model=model or config.TEXT_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
