from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


LLM_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "zhipu": {
        "model": "glm-5",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
    },
    "bailian": {
        "model": "qwen3.5-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
    "openai": {
        "model": "gpt-4.1-mini",
        "base_url": "https://api.openai.com/v1",
    },
}

PROVIDER_ALIASES = {
    "glm": "zhipu",
    "zhipuai": "zhipu",
    "bigmodel": "zhipu",
    "aliyun": "bailian",
    "dashscope": "bailian",
    "qwen": "bailian",
    "openai-compatible": "openai",
    "compatible": "openai",
}


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    backend_dir: Path
    project_root: Path
    llm_provider: str
    llm_model: str
    llm_api_key: str | None
    llm_base_url: str
    component_char_limit: int = 20_000
    terminal_timeout_seconds: int = 30
    context_max_tokens: int = 50_000
    context_token_reserve: int = 8_000
    context_max_messages: int = 50
    context_keep_head_messages: int = 3
    context_keep_recent_tool_results: int = 3
    context_tool_results_budget_bytes: int = 200_000
    context_preview_chars: int = 2_000
    context_summary_max_tokens: int = 2_000
    recovery_max_retries: int = 3
    recovery_initial_delay_ms: int = 500
    recovery_max_delay_ms: int = 8_000
    recovery_max_continuations: int = 3
    recovery_default_max_output_tokens: int = 8_192
    recovery_escalated_max_output_tokens: int = 65_536
    llm_fallback_model: str | None = None
    memory_enabled: bool = True
    memory_selector_max_items: int = 5
    memory_side_call_timeout_seconds: int = 20
    memory_extract_input_chars: int = 12_000
    memory_extract_max_items: int = 3


def _load_paths() -> tuple[Path, Path, Path]:
    config_dir = Path(__file__).resolve().parent
    backend_dir = config_dir.parent
    project_root = backend_dir.parent
    load_dotenv(config_dir / ".env")
    return config_dir, backend_dir, project_root


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _provider() -> str:
    value = (os.getenv("LLM_PROVIDER") or "zhipu").strip().lower()
    value = PROVIDER_ALIASES.get(value, value)
    return value if value in LLM_PROVIDER_DEFAULTS else "zhipu"


def _api_key(provider: str) -> str | None:
    aliases = {
        "zhipu": ("LLM_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"),
        "bailian": ("LLM_API_KEY", "BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
        "deepseek": ("LLM_API_KEY", "DEEPSEEK_API_KEY"),
        "openai": ("LLM_API_KEY", "OPENAI_API_KEY"),
    }
    return _first_env(*aliases[provider])


def _model(provider: str) -> str:
    aliases = {
        "zhipu": ("LLM_MODEL", "ZHIPU_MODEL"),
        "bailian": ("LLM_MODEL", "BAILIAN_MODEL"),
        "deepseek": ("LLM_MODEL", "DEEPSEEK_MODEL"),
        "openai": ("LLM_MODEL",),
    }
    return _first_env(*aliases[provider]) or LLM_PROVIDER_DEFAULTS[provider]["model"]


def _base_url(provider: str) -> str:
    aliases = {
        "zhipu": ("LLM_BASE_URL", "ZHIPU_BASE_URL"),
        "bailian": ("LLM_BASE_URL", "BAILIAN_BASE_URL"),
        "deepseek": ("LLM_BASE_URL", "DEEPSEEK_BASE_URL"),
        "openai": ("LLM_BASE_URL", "OPENAI_BASE_URL"),
    }
    return _first_env(*aliases[provider]) or LLM_PROVIDER_DEFAULTS[provider]["base_url"]


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_dir, backend_dir, project_root = _load_paths()
    provider = _provider()
    return Settings(
        config_dir=config_dir,
        backend_dir=backend_dir,
        project_root=project_root,
        llm_provider=provider,
        llm_model=_model(provider),
        llm_api_key=_api_key(provider),
        llm_base_url=_base_url(provider),
        context_max_tokens=_positive_int_env("CONTEXT_MAX_TOKENS", 50_000),
        context_token_reserve=_positive_int_env("CONTEXT_TOKEN_RESERVE", 8_000),
        context_max_messages=_positive_int_env("CONTEXT_MAX_MESSAGES", 50),
        context_keep_head_messages=_positive_int_env("CONTEXT_KEEP_HEAD_MESSAGES", 3),
        context_keep_recent_tool_results=_positive_int_env(
            "CONTEXT_KEEP_RECENT_TOOL_RESULTS", 3
        ),
        context_tool_results_budget_bytes=_positive_int_env(
            "CONTEXT_TOOL_RESULTS_BUDGET_BYTES", 200_000
        ),
        context_preview_chars=_positive_int_env("CONTEXT_PREVIEW_CHARS", 2_000),
        context_summary_max_tokens=_positive_int_env(
            "CONTEXT_SUMMARY_MAX_TOKENS", 2_000
        ),
        recovery_max_retries=_positive_int_env("RECOVERY_MAX_RETRIES", 3),
        recovery_initial_delay_ms=_positive_int_env(
            "RECOVERY_INITIAL_DELAY_MS", 500
        ),
        recovery_max_delay_ms=_positive_int_env("RECOVERY_MAX_DELAY_MS", 8_000),
        recovery_max_continuations=_positive_int_env(
            "RECOVERY_MAX_CONTINUATIONS", 3
        ),
        recovery_default_max_output_tokens=_positive_int_env(
            "RECOVERY_DEFAULT_MAX_OUTPUT_TOKENS", 8_192
        ),
        recovery_escalated_max_output_tokens=_positive_int_env(
            "RECOVERY_ESCALATED_MAX_OUTPUT_TOKENS", 65_536
        ),
        llm_fallback_model=_first_env("LLM_FALLBACK_MODEL"),
        memory_enabled=_bool_env("MEMORY_ENABLED", True),
        memory_selector_max_items=min(
            _positive_int_env("MEMORY_SELECTOR_MAX_ITEMS", 5),
            5,
        ),
        memory_side_call_timeout_seconds=_positive_int_env(
            "MEMORY_SIDE_CALL_TIMEOUT_SECONDS", 20
        ),
        memory_extract_input_chars=_positive_int_env(
            "MEMORY_EXTRACT_INPUT_CHARS", 12_000
        ),
        memory_extract_max_items=_positive_int_env(
            "MEMORY_EXTRACT_MAX_ITEMS", 3
        ),
    )
