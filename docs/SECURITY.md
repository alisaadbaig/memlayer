# memlayer Security Guide

Everything about protecting your memories: secure mode, passwords, guardrails,
and how memlayer defends the model against malicious memory content.

## 1. Secure mode

Secure mode password-locks a profile's memory. While locked, **every** memory
operation is refused — commands, the Python API, and prompt injection alike.
The model literally knows nothing about you until you unlock.

Turn it on when creating the store (per database + profile):

```python
store = MemoryStore("mem.db", user_id="ali", secure=True)
```
or from the CLI: `memlayer chat --secure`

### Setting a password (first time)

The **first** `/enable` on a secure profile sets the password:

```
/enable mypassword123        ← inline
/enable                      ← bare, in a terminal: hidden typing prompt
```

Minimum 4 characters. Each profile (`user_id`) has its own password.

### Daily use

| Command | Effect |
|---|---|
| `/enable <password>` | Unlock for this session |
| `/enable` (bare, terminal) | Same, with hidden typing |
| `/lock` | Lock immediately |
| quit / restart | Always relocks automatically |

### Changing the password

Requires the current password; memories are untouched:

```
/passwd oldpass newpass      ← inline
/passwd                      ← bare: hidden prompts + confirmation
```

Rules: wrong current password → "Password unchanged"; new password under
4 chars → refused; the change takes effect immediately (old password stops
working, new one persists across restarts).

### Forgot the password? (reset)

There is **no recovery mechanism — by design**. A backdoor for you is a
backdoor for an attacker. Your options:

1. **Restore a backup** you made while unlocked (`/export backup.json`
   → new database → `/import backup.json`). Make backups a habit.
2. **Manual reset** (requires file access): delete the password record —
   memories survive, and the next `/enable` sets a fresh password:
   ```bash
   sqlite3 mem.db "DELETE FROM settings WHERE user_id='ali';"
   ```

That option 2 exists highlights the current boundary, so read the next
section carefully.

### What secure mode does and does not protect

**Protects against:** the app and the model. While locked, nothing can read,
write, list, search, export, or inject memories — enforced inside the store
itself (`PermissionError`), not just in the chat layer — and your password is
stored only as a salted PBKDF2-HMAC-SHA256 hash (200,000 iterations),
verified in constant time. The raw password appears nowhere in the file.

**Does not yet protect against:** someone with the `.db` file. Memory *text*
is stored unencrypted, so file access means memory access (never password
access). Encryption-at-rest is a planned feature; it is genuinely hard to
combine with full-text search, so we would rather ship it right than fast.
Until then: protect the file like any private document (disk encryption,
file permissions).

## 2. Guardrails: filtering what enters memory

```python
from memlayer import Guardrails, MemoryStore
guard = Guardrails(pii_mode="redact", blocklist=["projectx"], max_length=2000)
store = MemoryStore("mem.db", guardrails=guard)
```

**PII detection** (email, phone, SSN, IP, API keys, Luhn-verified credit
cards) with four modes: `allow` | `warn` (default) | `redact` (store
`[EMAIL]` instead of the value) | `block` (refuse the save). Regex-based, so
it catches structured formats, not free-text like "ali at example dot com".

**NER (optional)** — ML detection of organizations/locations via spaCy
(`pip install "memlayer[ner]"`). Defaults to ORG + GPE; add `"PERSON"`
deliberately — redacting names defeats a personal-memory library.

**Prohibited terms** — static `blocklist=[...]` plus a persistent list
managed in chat: `/block <term>`, `/unblock <term>`, `/blocked`. The
persistent list survives restarts and applies even with no Guardrails object.

⚠️ Guardrails are **opt-in**: a store created without a `Guardrails` object
(and with an empty `/block` list) filters nothing. They also filter on the
way **in** — enabling them later does not clean previously saved memories.

## 3. Defending the model from memory (injection)

Memories are user data, and data can be hostile. memlayer layers defenses:

1. **Untrusted framing** — memories enter the prompt inside
   `BEGIN/END UNTRUSTED USER MEMORY` markers with an explicit instruction
   that the content is data, never commands.
2. **Fence integrity** — any occurrence of those marker strings *inside*
   memory text is neutralized to `[removed marker]` at injection time,
   unconditionally, so data cannot fake the fence boundary.
3. **Injection shield** (with guardrails on) — memories matching known
   injection patterns ("ignore previous instructions", ...) are dropped
   before reaching the prompt at all.
4. **Provenance weighting** — every memory records its source:
   `user_explicit` (1.0) > `user_conversation` (0.8) > `tool_output` (0.7,
   e.g. `/task` URL fetches) > `model_inferred` (0.4). Web-fetched content
   never carries the authority of what you typed yourself.

No prompt-level defense is perfect against every model; these layers reduce
risk, they don't abolish it. Treat `/task` URLs like you treat links: fetch
sources you trust.

## 4. Practical hardening checklist

- Use `secure=True` for any profile holding personal facts
- Run with `--pii redact` (or `block` in strict environments)
- `/block` your project codenames and other never-store terms
- `/export` backups regularly *while unlocked*; store them as carefully as
  the database itself (exports are plain JSON)
- One profile per person; profiles are isolated, each with its own password
- Keep the `.db` on an encrypted disk until encryption-at-rest ships

## 5. Reporting security issues

Found a bypass? Please open a GitHub issue (or contact the maintainer
privately for sensitive findings). This project has a track record of
reproducing, fixing, and regression-testing reported issues quickly — see
CHANGELOG.md for the history.
