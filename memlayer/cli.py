"""
memlayer.cli — run a memory chat from the terminal, no code needed.

    memlayer chat --backend ollama --model llama3.2
    memlayer chat --backend openai --model llama-3.2 --url http://localhost:8000/v1
    memlayer chat --backend hf --model Qwen/Qwen2.5-1.5B-Instruct
    memlayer chat --secure --pii redact
"""

from __future__ import annotations

import argparse

from .store import MemoryStore
from .agent import MemoryAgent
from .guardrails import Guardrails
from .adapters import OllamaBackend, OpenAICompatBackend, HuggingFaceBackend


def main() -> None:
    p = argparse.ArgumentParser(prog="memlayer",
                                description="Persistent memory for any LLM.")
    sub = p.add_subparsers(dest="cmd", required=True)

    chat = sub.add_parser("chat", help="interactive memory chat")
    chat.add_argument("--backend", choices=["ollama", "openai", "hf", "none"],
                      default="ollama")
    chat.add_argument("--model", default="llama3.2")
    chat.add_argument("--url", default=None,
                      help="server URL (ollama host or openai base_url)")
    chat.add_argument("--db", default="memlayer.db")
    chat.add_argument("--user", default="default")
    chat.add_argument("--secure", action="store_true",
                      help="password-protect memory")
    chat.add_argument("--pii", choices=["allow", "warn", "redact", "block"],
                      default="warn", help="PII guardrail mode")
    chat.add_argument("--no-stream", action="store_true")

    args = p.parse_args()

    guard = Guardrails(pii_mode=args.pii)
    store = MemoryStore(args.db, user_id=args.user, secure=args.secure,
                        guardrails=guard)

    backend = None
    if args.backend == "ollama":
        backend = OllamaBackend(args.model,
                                host=args.url or "http://localhost:11434")
    elif args.backend == "openai":
        backend = OpenAICompatBackend(
            args.model, base_url=args.url or "http://localhost:8000/v1")
    elif args.backend == "hf":
        backend = HuggingFaceBackend(args.model)

    MemoryAgent(store, backend).repl(stream=not args.no_stream)


if __name__ == "__main__":
    main()
