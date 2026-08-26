"""
memlayer — Give ANY LLM permanent memory. No fine-tuning, no model changes.
"""

from .store import MemoryStore
from .agent import MemoryAgent
from .guardrails import Guardrails
from .adapters import (
    BaseBackend, OllamaBackend, OpenAICompatBackend,
    AnthropicBackend, HuggingFaceBackend,
)

__version__ = "0.5.0"
__all__ = ["MemoryStore", "MemoryAgent", "Guardrails", "BaseBackend",
           "OllamaBackend", "OpenAICompatBackend", "AnthropicBackend",
           "HuggingFaceBackend"]
