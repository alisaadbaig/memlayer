# 🧠 memlayer

**SQLite for AI memory.** Give any LLM permanent memory — no fine-tuning, no model changes, zero required dependencies.

Type `/save my name is Ali and I am 36`, and from that moment on, *every* model you use — Ollama, vLLM, Hugging Face, anything — knows it. Memory lives in a fast local SQLite database and is injected into the prompt automatically (RAG-style).

```
You: /save my name is Ali and I am 36 years old
memlayer: Saved [a1b2c3d4]

You: how old am I?
Assistant: You're 36, Ali!
```

## Why memlayer?

- ⚡ **Fast** — SQLite FTS5 full-text search with BM25 ranking. Searches thousands of memories in microseconds, no vector DB required.
- 🔌 **Works with everything** — Ollama, vLLM, LM Studio, llama.cpp server, OpenAI, Groq, or any Hugging Face model in-process. One line to switch backends; your memories follow you.
- 📦 **Zero required dependencies** — core is pure Python standard library. Optional extras add semantic search and in-process HF models.
- 👥 **Multi-user** — isolate memories per `user_id` in one database.
- 🔒 **Private by design** — local `.db` file you own, plus optional password-locked secure mode.

## Install

```bash
pip install memlayer                # core (stdlib only)
pip install "memlayer[semantic]"    # + embedding reranking
pip install "memlayer[huggingface]" # + run HF models in-process
```

## Quickstart

### Ollama
```python
from memlayer import MemoryStore, MemoryAgent, OllamaBackend

agent = MemoryAgent(MemoryStore("mem.db", user_id="ali"),
                    OllamaBackend("llama3.2"))
agent.repl()   # interactive chat with /save, /memories, /forget, /clear
```

### vLLM (or any OpenAI-compatible server)
```python
from memlayer import MemoryStore, MemoryAgent, OpenAICompatBackend

backend = OpenAICompatBackend("meta-llama/Llama-3.2-3B-Instruct",
                              base_url="http://localhost:8000/v1")
agent = MemoryAgent(MemoryStore("mem.db"), backend)
print(agent.ask("what do you know about me?"))
```

### Claude (Anthropic)
```python
from memlayer import MemoryStore, MemoryAgent, AnthropicBackend
# export ANTHROPIC_API_KEY=sk-ant-...
agent = MemoryAgent(MemoryStore("mem.db"), AnthropicBackend("claude-sonnet-4-6"))
agent.repl()
```

### OpenAI (ChatGPT models)
```python
import os
from memlayer import MemoryStore, MemoryAgent, OpenAICompatBackend
backend = OpenAICompatBackend("gpt-4o-mini",
                              base_url="https://api.openai.com/v1",
                              api_key=os.environ["OPENAI_API_KEY"])
agent = MemoryAgent(MemoryStore("mem.db"), backend)
```

Ready-to-run scripts for every backend are in [`examples/`](examples/): `chat_ollama.py`, `chat_vllm.py`, `chat_openai.py`, `chat_claude.py`, `chat_hf.py` — all sharing the same memory format, so one `mem.db` follows you across every model.

### Hugging Face (in-process, no server)
```python
from memlayer import MemoryStore, MemoryAgent, HuggingFaceBackend

agent = MemoryAgent(MemoryStore("mem.db"),
                    HuggingFaceBackend("Qwen/Qwen2.5-1.5B-Instruct"))
```

## Commands

| Command | What it does |
|---|---|
| `/save <keyword> <fact>` | Remember a fact under a keyword, e.g. `/save age Ali is 36` |
| `/memories` | List everything saved (with keywords) |
| `/forget <id>` | Delete one memory |
| `/clear` | Delete all memories |
| `/enable <password>` | Unlock memory in secure mode (first use sets the password) |
| `/lock` | Lock memory again |

## 🛡️ Guardrails

```python
from memlayer import Guardrails, MemoryStore
guard = Guardrails(pii_mode="redact",          # allow | warn | redact | block
                   blocklist=["secretproject"], max_length=2000)
store = MemoryStore("mem.db", guardrails=guard)
```

- **PII filter** — detects emails, phones, credit cards (Luhn-verified), SSNs, IPs, API keys; block, redact, or warn
- **Prompt-injection shield** — memories containing "ignore previous instructions"-style patterns never reach the model prompt
- **Prohibited terms** — static `blocklist=[...]` plus a persistent list managed from chat: `/block projectx`, `/unblock projectx`, `/blocked` (survives restarts, works even without a Guardrails object)
- **NER (optional)** — ML detection of organizations, locations, and names that regex can't catch, via spaCy: `Guardrails(ner=True)`. Defaults to ORG + GPE only — add `"PERSON"` to `ner_entities` deliberately, since redacting names defeats a *personal* memory. `pip install "memlayer[ner]" && python -m spacy download en_core_web_sm`
- **Length limits** — full control over what gets stored

## ⚡ Also included

Streaming responses • conversation history • contradiction detection with `/replace` and `/save!` • `/undo` • `/search` • `/stats` • `/export` & `/import` • memory expiry (`/save --expires 30d ...`) • multi-profile (`/profile work`) • CLI: `memlayer chat --backend ollama --model llama3.2`

## 🔐 Secure mode

Turn it on with one flag:

```python
store = MemoryStore("mem.db", user_id="ali", secure=True)
```

While locked, `/save`, `/memories`, `/forget`, `/clear` are refused **and no
memories are injected into the prompt** — the model knows nothing about you
until you type `/enable <password>`. The first `/enable` sets the password;
passwords are stored as salted PBKDF2-SHA256 hashes (200k iterations), never
plain text. Restarting the app locks memory again automatically.

## How it works

1. `/save` writes the fact to SQLite (with an FTS5 index maintained by triggers).
2. On every normal message, memlayer runs a BM25 search over your memories, optionally reranks with embeddings, and prepends the top hits to the system prompt.
3. The model answers as if it always knew you. Switch models freely — memory is model-agnostic.

## 🧬 Memory lifecycle (v0.5)

Memories aren't just rows — they have a life:

- **Supersession** — `/replace <id> <kw> <fact>` marks the old fact `superseded` (kept as history, never injected) instead of deleting it. `/history city` shows the full chain: `Austin (old) → Dallas (ACTIVE)`. Retrieval only ever returns current truth.
- **Provenance & confidence** — every memory records its `source` (`user_explicit` 1.0, `user_conversation` 0.8, `tool_output` 0.7, `model_inferred` 0.4) so explicit facts outrank guesses.
- **Untrusted framing** — memories are injected inside `BEGIN/END UNTRUSTED USER MEMORY` markers with an explicit "data, not instructions" preamble: defense-in-depth on top of the injection shield.
- **Opt-in auto-extraction** — `/auto on` makes the model propose one memorable fact after your messages ("Worth remembering? Type: /save family ..."). Nothing is ever saved without your confirmation, and there's zero extra inference cost when off.
- **Recency-aware retrieval** — near-tied results prefer newer, higher-confidence memories; `last_accessed_at` tracks which memories actually get used.

## Roadmap

- [ ] Recall benchmark suite (temporal correctness, contradiction handling, injection resistance)
- [ ] Memory decay / importance scoring
- [ ] Streaming responses
- [ ] REST API server mode (`memlayer serve`)
- [ ] LangChain / LlamaIndex integrations

## License

MIT — do whatever you want, a star ⭐ is appreciated.
