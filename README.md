# 🧠 memlayer

**Give any LLM permanent memory. No fine-tuning. No model changes. Zero required dependencies.**

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

## Roadmap

- [ ] Automatic memory extraction (model decides what to save)
- [ ] Memory decay / importance scoring
- [ ] Streaming responses
- [ ] REST API server mode (`memlayer serve`)
- [ ] LangChain / LlamaIndex integrations

## License

MIT — do whatever you want, a star ⭐ is appreciated.
