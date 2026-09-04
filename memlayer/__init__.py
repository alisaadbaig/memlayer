"""
memlayer — Give ANY LLM permanent memory. No fine-tuning, no model changes.
"""

from .store import MemoryStore
from .agent import MemoryAgent
from .tasks import TaskManager, fetch_url_text
from .embeddings import SentenceTransformerEmbedder, OllamaEmbedder
from .guardrails import Guardrails
from .adapters import (
    BaseBackend, OllamaBackend, OpenAICompatBackend,
    AnthropicBackend, HuggingFaceBackend,
)

__version__ = "0.7.0"
__all__ = ["MemoryStore", "MemoryAgent", "Guardrails", "BaseBackend",
           "OllamaBackend", "OpenAICompatBackend", "AnthropicBackend",
           "HuggingFaceBackend", "TaskManager", "fetch_url_text",
           "SentenceTransformerEmbedder", "OllamaEmbedder"]
