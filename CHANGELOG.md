# Changelog

All notable changes to **memlayer** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- `AnthropicBackend` — Claude via the native Anthropic Messages API (stdlib-only, streaming supported, reads `ANTHROPIC_API_KEY` from the environment).
- Example scripts for every backend in `examples/`: `chat_vllm.py`, `chat_openai.py` (ChatGPT models), `chat_claude.py`, `chat_hf.py` — all sharing one memory format.

## [0.5.0] — 2026-08-26

### Added
- **Memory lifecycle & supersession** — `/replace <id> <kw> <fact>` now marks the old memory `superseded` (kept as history, never injected or retrieved) instead of deleting it. Retrieval returns only current truth.
- `/history <keyword>` — shows the full version chain for a keyword: `old [Austin] → ACTIVE [Dallas]`, with source and confidence per entry.
- **Provenance & confidence** — every memory records its `source` (`user_explicit` 1.0, `user_conversation` 0.8, `tool_output` 0.7, `model_inferred` 0.4) and a confidence score; both usable via the Python API.
- **Untrusted-memory prompt framing** — memories are injected inside `BEGIN/END UNTRUSTED USER MEMORY` markers with an explicit "data, not instructions" preamble (defense-in-depth on top of the injection shield).
- **Opt-in auto-extraction** — `/auto on` makes the model propose at most one memorable fact after a user message (`[memory suggestion] Worth remembering? Type: /save ...`). Nothing is saved without explicit confirmation; failures never break the chat; off by default.
- `last_accessed_at` tracking — memories are stamped when actually injected into a prompt.
- Automatic schema migration — databases created by v0.1–v0.4 upgrade in place on first open.

### Changed
- Retrieval ranking: near-tied BM25 results now prefer newer, higher-confidence memories.
- `/stats` reports the superseded count.
- FTS query rewritten with a ranked CTE (rank inside the index, join only top rows); `PRAGMA synchronous=NORMAL` under WAL. Typical searches ~0.9 ms over 5,000 memories; worst case (query matching every row) ~5 ms.
- README tagline: **"SQLite for AI memory."**

### Fixed
- Schema-migration ordering bug: index on `status` was created before the column existed when opening old databases.
- Flaky speed tests replaced with honest dual gates: <2 ms typical, <10 ms pathological.

## [0.4.0] — 2026-08-26

### Added
- **Persistent prohibited terms** — `/block <term>`, `/unblock <term>`, `/blocked`; stored in the database, survive restarts, and are enforced even when no `Guardrails` object is configured.
- **Optional NER** (`pip install "memlayer[ner]"`, spaCy) — ML detection of entities regex can't catch. Defaults to `ORG` + `GPE` only; `PERSON` must be added deliberately, since redacting names defeats a personal-memory library. Follows the same `pii_mode` (warn/redact/block). Graceful fallback message when spaCy or the model is missing.
- Repository infrastructure: `.gitignore` (protects `*.db` user memories), MIT `LICENSE`, GitHub Actions CI (Python 3.9/3.11/3.12, 60 tests).

## [0.3.0] — 2026-08-24

### Added
- **Guardrails module** — PII detection (email, phone, SSN, IP, API keys, Luhn-verified credit cards) with `allow | warn | redact | block` modes; static blocklist; length limits; **prompt-injection shield** filtering retrieved memories before they reach the model.
- **Streaming responses** for Ollama and OpenAI-compatible backends (`chat_stream`, `ask_stream`; REPL prints chunks live).
- **Conversation history** — rolling multi-turn window passed to backends.
- **Contradiction detection** — similar `/save` is intercepted with `/replace` and `/save!` options.
- Commands: `/search`, `/undo`, `/stats`, `/export`, `/import`, `/profile` (isolated multi-profile memory).
- **Memory expiry** — `/save --expires 30d ...` (m/h/d/w).
- Hidden password entry via `getpass` when typing `/enable` alone in a terminal.
- **CLI entry point** — `memlayer chat --backend ollama|openai|hf --secure --pii redact ...`.
- Token-budget-aware context (`max_context_tokens`, default 600).
- Adapter retries with exponential backoff and clean `ConnectionError`s; store transactions with rollback.
- Test suites: 35 pytest unit tests + 25-use-case runner (`tests/test_all_usecases.py`, CI-friendly exit codes, optional `--with-model` live tests).

### Changed
- Embedding model lazy-loads on first use.

## [0.2.0] — 2026-08-20

### Added
- **Secure mode** — `MemoryStore(..., secure=True)`: memory starts locked; `/enable <password>` sets (first use) or verifies; `/lock` re-locks. While locked, all memory commands are refused **and no memories are injected into the prompt**. Passwords stored as salted PBKDF2-HMAC-SHA256 (200k iterations), verified in constant time; unlock is per-session. Lock enforced in the store itself (`PermissionError`), not just the chat layer.
- **Keyword syntax** — `/save <keyword> <fact>`, with quoted multi-word keywords (`/save "favorite food" ...`); keywords indexed for search and shown in `/memories`.

## [0.1.0] — 2026-08-20

### Added
- Initial release: SQLite + FTS5 storage with BM25 ranking (WAL mode, multi-user isolation, atomic writes).
- Backends behind one interface: `OllamaBackend`, `OpenAICompatBackend` (vLLM, LM Studio, llama.cpp server, OpenAI, Groq), `HuggingFaceBackend` (in-process transformers).
- RAG-style context injection into the system prompt; model-agnostic memory ("memory follows the model").
- Commands: `/save`, `/memories`, `/forget`, `/clear`, `/help`; interactive REPL.
- Optional semantic reranking via sentence-transformers (`memlayer[semantic]`).
- Zero required dependencies (pure standard library core); pip-installable (`pyproject.toml`).
