from __future__ import annotations

import logging
import threading
from typing import cast

from .base import LLMProvider, Message, fold_system

log = logging.getLogger(__name__)


class LlamaCppProvider(LLMProvider):
    def __init__(
        self,
        model_path: str | None = None,
        repo_id: str = "ggml-org/gemma-3-1b-it-GGUF",  # verify repo/filename
        filename: str = "*Q4_K_M.gguf",
        device: str = "auto",  # "auto" | "cpu" | "gpu"
        n_gpu_layers: int | None = None,  # partial offload override
        n_ctx: int = 2048,
        seed: int = 0,
    ):
        try:
            import llama_cpp
        except ImportError as e:
            raise ImportError("Install with: pip install yourpkg[llama]") from e
        self._lc = llama_cpp
        self._lock = threading.Lock()  # a Llama instance is not thread-safe

        layers = self._resolve_layers(device, n_gpu_layers)
        try:
            self.llm = self._load(model_path, repo_id, filename, layers, n_ctx, seed)
            self.device = "gpu" if layers != 0 else "cpu"
        except Exception:
            if layers == 0:
                raise
            log.warning("GPU load failed (likely out of VRAM); falling back to CPU")
            self.llm = self._load(model_path, repo_id, filename, 0, n_ctx, seed)
            self.device = "cpu"

    def _resolve_layers(self, device: str, n_gpu_layers: int | None) -> int:
        if device == "cpu":
            return 0
        supported = self._lc.llama_supports_gpu_offload()
        if device == "gpu" and not supported:
            raise RuntimeError(
                "device='gpu' requested but llama-cpp-python was built without GPU support."
            )
        if not supported:
            return 0
        return -1 if n_gpu_layers is None else n_gpu_layers

    def _load(self, model_path, repo_id, filename, layers, n_ctx, seed):
        kwargs = dict(n_ctx=n_ctx, n_gpu_layers=layers, seed=seed, verbose=False)
        if model_path:
            return self._lc.Llama(model_path=model_path, **kwargs)
        return self._lc.Llama.from_pretrained(repo_id=repo_id, filename=filename, **kwargs)

    def _complete(self, messages: list[Message], schema: dict, max_tokens: int) -> str:
        with self._lock:
            out = self.llm.create_chat_completion(
                messages=fold_system(messages),
                response_format={"type": "json_object", "schema": schema},
                temperature=0.0,
                max_tokens=max_tokens,
            )
        return cast(str, out["choices"][0]["message"]["content"])
