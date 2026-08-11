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
    )
