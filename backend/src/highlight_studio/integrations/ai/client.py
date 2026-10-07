from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .ollama import OllamaClient
from ...core.settings import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    SERVER_OLLAMA_URL,
    WEB_ACCOUNTS_ENABLED,
)


def effective_ai_engine(settings: dict[str, Any]) -> str:
    """The application intentionally uses local/server-managed Ollama only."""
    return "ollama"


def openai_api_key(settings: dict[str, Any]) -> str:
    # Kept only for backwards compatibility with old projects/tests.
    return ""


def effective_text_model(settings: dict[str, Any]) -> str:
    return str(settings.get("text_model") or DEFAULT_TEXT_MODEL)


def effective_vision_model(settings: dict[str, Any]) -> str:
    return str(settings.get("vision_model") or DEFAULT_VISION_MODEL)


def effective_ollama_url(settings: dict[str, Any]) -> str:
    """Resolve Ollama endpoint without allowing project-level SSRF in web mode.

    Desktop remains flexible because the user controls the local machine. In
    multi-user web mode the endpoint is server-managed via
    HIGHLIGHT_STUDIO_OLLAMA_URL and project settings cannot redirect requests
    to arbitrary hosts.
    """
    if WEB_ACCOUNTS_ENABLED:
        return SERVER_OLLAMA_URL
    return str(settings.get("ollama_url") or DEFAULT_OLLAMA_URL).strip() or DEFAULT_OLLAMA_URL


def _cancel_check_from_file(cancel_file: Path | None) -> Callable[[], bool] | None:
    if cancel_file is None:
        return None
    path = Path(cancel_file)
    return path.exists


class OpenAIClient:
    """Disabled stub. The project uses Ollama only."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings

    def check(self, text_model: str | None = None, vision_model: str | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "engine": "disabled",
            "error": "OpenAI API отключён в локальной сборке. Используется только Ollama Local.",
            "models": [],
        }

    def generate_json(self, *args, **kwargs) -> dict[str, Any]:
        raise RuntimeError("OpenAI API отключён. Используй Ollama Local.")

    def generate_json_with_images(self, *args, **kwargs) -> dict[str, Any]:
        raise RuntimeError("OpenAI API отключён. Используй Ollama Vision через qwen3-vl.")


class HybridAIClient:
    """Compatibility wrapper routing text and visual AI requests to Ollama."""

    def __init__(
        self,
        settings: dict[str, Any],
        cancel_check: Callable[[], bool] | None = None,
        project_dir: Path | None = None,
    ):
        self.settings = settings
        self.engine = "ollama"
        self.project_dir = Path(project_dir) if project_dir else None
        self.ollama = OllamaClient(
            effective_ollama_url(settings),
            keep_alive=settings.get("ollama_keep_alive", "5m"),
            num_ctx=settings.get("ollama_num_ctx", 4096),
            cancel_check=cancel_check,
            settings=settings,
            project_dir=self.project_dir,
        )

    def check(self, text_model: str | None = None, vision_model: str | None = None) -> dict[str, Any]:
        return {
            "engine": "ollama",
            **self.ollama.check(
                self.settings.get("text_model", DEFAULT_TEXT_MODEL),
                self.settings.get("vision_model", DEFAULT_VISION_MODEL),
            ),
        }

    def warmup(self, model: str | None = None, timeout: int = 120) -> dict[str, Any]:
        return self.ollama.warmup(model=model or self.settings.get("text_model", DEFAULT_TEXT_MODEL), timeout=timeout)

    def unload(self, model: str | None = None, timeout: int = 30) -> dict[str, Any]:
        return self.ollama.unload(model=model or self.settings.get("text_model", DEFAULT_TEXT_MODEL), timeout=timeout)

    def generate_json(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int = 240,
        *,
        operation: str = "text_json",
        trace_context: dict[str, Any] | None = None,
        collection_key: str | None = None,
        expected_ids: set[int] | None = None,
        required_item_fields: set[str] | None = None,
        activity_callback: Callable[[float, int], None] | None = None,
    ) -> dict[str, Any]:
        return self.ollama.generate_json(
            prompt,
            model=model or self.settings.get("text_model", DEFAULT_TEXT_MODEL),
            timeout=timeout,
            operation=operation,
            trace_context=trace_context,
            collection_key=collection_key,
            expected_ids=expected_ids,
            required_item_fields=required_item_fields,
            activity_callback=activity_callback,
        )

    def generate_json_with_images(
        self, prompt: str, image_paths: list[Path], model: str | None = None, timeout: int = 300
    ) -> dict[str, Any]:
        return self.ollama.generate_json_with_images(
            prompt,
            image_paths,
            model=model or self.settings.get("vision_model", DEFAULT_VISION_MODEL),
            timeout=timeout,
        )


def make_ai_client(settings: dict[str, Any], cancel_file: Path | None = None) -> HybridAIClient:
    project_dir = Path(cancel_file).parent if cancel_file is not None else None
    return HybridAIClient(settings, cancel_check=_cancel_check_from_file(cancel_file), project_dir=project_dir)
