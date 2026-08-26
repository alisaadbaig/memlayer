"""
memlayer.agent — slash commands + retrieval + history + any backend.
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path
from typing import Optional

from .store import MemoryStore, parse_duration
from .adapters import BaseBackend

HELP = (
    "Commands:\n"
    "  /save <keyword> <fact>        — remember a fact  (quote multi-word keywords)\n"
    "  /save --expires 30d <kw> <f>  — fact that auto-deletes (m/h/d/w)\n"
    "  /save! ...                    — force-save even if a similar memory exists\n"
    "  /replace <id> <kw> <fact>     — overwrite an old memory\n"
    "  /undo                         — delete the last saved memory\n"
    "  /search <query>               — search your memories directly\n"
    "  /memories                     — list everything saved\n"
    "  /forget <id>   /clear         — delete one / all\n"
    "  /stats                        — memory statistics\n"
    "  /export [file] /import <file> — backup / restore JSON\n"
    "  /block <term> /unblock <term>  — manage prohibited terms\n"
    "  /history <keyword>            — see current + superseded versions\n"
    "  /auto on|off                  — suggest memories from conversation\n"
    "  /blocked                      — list prohibited terms\n"
    "  /profile <name>               — switch memory profile\n"
    "  /enable [password]  /lock     — secure mode unlock / lock\n"
    "  /help                         — this help"
)

LOCKED_MSG = ("Memory is locked (secure mode). "
              "Unlock with: /enable <password>")


def _split_keyword(payload: str) -> tuple[str, str]:
    """('age', 'Ali is 36') | quoted: ('favorite food', 'Ali loves biryani')"""
    payload = payload.strip()
    if not payload:
        return "", ""
    if payload[0] in ('"', "'"):
        end = payload.find(payload[0], 1)
        if end == -1:
            return "", ""
        return payload[1:end].strip(), payload[end + 1:].strip()
    parts = payload.split(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


class MemoryAgent:
    def __init__(self, store: MemoryStore, backend: Optional[BaseBackend] = None,
                 system_prompt: str = "You are a helpful assistant.",
                 top_k: int = 5, history_turns: int = 6,
                 max_context_tokens: Optional[int] = 600):
        self.store = store
        self.backend = backend
        self.system_prompt = system_prompt
        self.top_k = top_k
        self.max_context_tokens = max_context_tokens
        self.history: deque = deque(maxlen=history_turns * 2)
        self._last_saved_id: Optional[str] = None
        self.auto_extract = False

    # ------------------------------------------------------------------
    # save helper (expiry flag, contradiction check, force)
    # ------------------------------------------------------------------

    def _do_save(self, payload: str, force: bool,
                 supersedes: str = None) -> str:
        expires_in = None
        m = re.match(r"--expires\s+(\S+)\s+(.*)", payload, re.DOTALL)
        if m:
            expires_in = parse_duration(m.group(1))
            if expires_in is None:
                return "Bad duration. Use e.g. 45m, 12h, 30d, 2w."
            payload = m.group(2)

        keyword, fact = _split_keyword(payload)
        if not keyword or not fact:
            return ("Usage: /save <keyword> <fact>\n"
                    'e.g.  /save age Ali is 36   |   /save "favorite food" ...')

        if not force:
            dup = self.store.find_similar(keyword, fact)
            if dup:
                return (f"A similar memory exists [{dup['id']}] "
                        f"({dup['keyword']}): \"{dup['text']}\"\n"
                        f"  /replace {dup['id']} {keyword} {fact}   — supersede it (kept as history)\n"
                        f"  /save! {keyword} {fact}                — keep both active")
        try:
            rec = self.store.save(fact, keyword=keyword, expires_in=expires_in,
                                  supersedes=supersedes)
        except (ValueError, PermissionError) as e:
            return str(e)
        self._last_saved_id = rec["id"]
        extra = f"  [{'; '.join(rec['warnings'])}]" if rec.get("warnings") else ""
        exp = f" (expires {rec['expires_at']})" if rec.get("expires_at") else ""
        sup = f" — superseded [{rec['supersedes']}]" if rec.get("supersedes") else ""
        return f'Saved [{rec["id"]}] ({rec["keyword"]}): "{rec["text"]}"{exp}{sup}{extra}'

    # ------------------------------------------------------------------

    def handle_command(self, text: str) -> Optional[str]:
        t = text.strip()
        low = t.lower()
        sec = self.store.security

        if low.startswith("/enable"):
            password = t[7:].strip()
            if not password and sys.stdin.isatty():
                import getpass
                password = getpass.getpass("Password (hidden): ")
            if not password:
                return "Usage: /enable <password>"
            return sec.enable(password)

        if low.startswith("/lock"):
            return sec.lock()

        if low.startswith("/profile"):
            name = t[8:].strip()
            if not name:
                return f"Current profile: {self.store.user_id}"
            self.store.switch_user(name)
            self.history.clear()
            state = " (locked)" if not sec.check() and \
                self.store.security.secure else ""
            return f"Switched to profile '{name}'{state}."

        if low.startswith("/help"):
            return HELP

        # everything below touches memory -> lock check
        needs_unlock = ("/save", "/replace", "/undo", "/search", "/memories",
                        "/list", "/forget", "/clear", "/stats", "/export",
                        "/import", "/block", "/unblock", "/blocked", "/history")
        if any(low.startswith(c) for c in needs_unlock) \
                and not self.store.security.check():
            return LOCKED_MSG

        if low.startswith("/blocked"):
            terms = self.store.list_blocked()
            return ("Prohibited terms: " + ", ".join(terms)) if terms \
                else "No prohibited terms set. Add one with /block <term>."

        if low.startswith("/block "):
            term = t[6:].strip()
            if not term:
                return "Usage: /block <term>"
            self.store.add_blocked(term)
            return (f'Added prohibited term "{term.lower()}". '
                    f"Saves containing it will be refused.")

        if low.startswith("/unblock"):
            term = t[8:].strip()
            if not term:
                return "Usage: /unblock <term>"
            return (f'Removed "{term.lower()}" from prohibited terms.'
                    if self.store.remove_blocked(term)
                    else f'"{term.lower()}" was not in the prohibited list.')

        if low.startswith("/save!"):
            return self._do_save(t[6:].strip(), force=True)
        if low.startswith("/save"):
            return self._do_save(t[5:].strip(), force=False)

        if low.startswith("/replace"):
            rest = t[8:].strip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /replace <id> <keyword> <fact>"
            mem_id, remainder = parts
            return self._do_save(remainder, force=True, supersedes=mem_id)

        if low.startswith("/history"):
            kw = t[8:].strip().strip('"').strip("'")
            if not kw:
                return "Usage: /history <keyword>"
            chain = self.store.history(kw)
            if not chain:
                return f'No memories under keyword "{kw}".'
            lines = []
            for m in chain:
                mark = "ACTIVE " if m["status"] == "active" else "old    "
                lines.append(f'{mark}[{m["id"]}] {m["text"]}  '
                             f'({m["created_at"]}, {m["source"]}, '
                             f'conf {m["confidence"]:.1f})')
            return f'History for "{kw}":\n' + "\n".join(lines)

        if low.startswith("/auto"):
            arg = t[5:].strip().lower()
            if arg == "on":
                self.auto_extract = True
                return ("Auto-extraction ON: after each reply I will suggest "
                        "facts worth saving. Nothing is saved without you.")
            if arg == "off":
                self.auto_extract = False
                return "Auto-extraction OFF."
            return f"Auto-extraction is {'ON' if self.auto_extract else 'OFF'}. Use /auto on|off."

        if low.startswith("/undo"):
            if not self._last_saved_id:
                return "Nothing to undo."
            ok = self.store.forget(self._last_saved_id)
            mid, self._last_saved_id = self._last_saved_id, None
            return f"Removed [{mid}]." if ok else "Already removed."

        if low.startswith("/search"):
            q = t[7:].strip()
            if not q:
                return "Usage: /search <query>"
            hits = self.store.search(q, top_k=5)
            if not hits:
                return "No matching memories."
            return "Matches:\n" + "\n".join(
                f"[{m['id']}] ({m['keyword']}) {m['text']}" for m in hits)

        if low.startswith(("/memories", "/list")):
            mems = self.store.all()
            if not mems:
                return "No memories saved yet."
            return "Saved memories:\n" + "\n".join(
                f"[{m['id']}] ({m['keyword']}) {m['text']}  ({m['created_at']})"
                for m in mems)

        if low.startswith("/forget"):
            mem_id = t[7:].strip()
            if not mem_id:
                return "Usage: /forget <memory id>"
            return (f"Forgot memory {mem_id}." if self.store.forget(mem_id)
                    else f"No memory found with id {mem_id}.")

        if low.startswith("/clear"):
            return f"Cleared {self.store.clear()} memories."

        if low.startswith("/stats"):
            s = self.store.stats()
            kws = ", ".join(f"{k}: {v}" for k, v in
                            sorted(s["by_keyword"].items())) or "-"
            return (f"Profile: {self.store.user_id}\n"
                    f"Memories: {s['count']}\nBy keyword: {kws}\n"
                    f"DB size: {s['db_bytes']/1024:.1f} KB\n"
                    f"Oldest: {s['oldest']}  Newest: {s['newest']}")

        if low.startswith("/export"):
            fname = t[7:].strip() or "memlayer_export.json"
            Path(fname).write_text(self.store.export_json(), encoding="utf-8")
            return f"Exported {self.store.count()} memories to {fname}"

        if low.startswith("/import"):
            fname = t[7:].strip()
            if not fname or not Path(fname).exists():
                return "Usage: /import <file.json>  (file must exist)"
            n = self.store.import_json(Path(fname).read_text(encoding="utf-8"))
            return f"Imported {n} memories."

        return None

    # ------------------------------------------------------------------


    EXTRACT_PROMPT = (
        "Review the user's last message. If it contains ONE new lasting fact "
        "about the user (identity, preference, plan, relationship, work), "
        "reply ONLY with JSON: {\"keyword\": \"one or two words\", "
        "\"fact\": \"the fact, third person\"}. "
        "If nothing is worth remembering long-term, reply ONLY: NONE")

    def _suggest_memory(self, user_input: str) -> str:
        """Ask the backend to propose one memory candidate. Returns a
        suggestion line or empty string. Never saves anything itself."""
        if not (self.auto_extract and self.backend):
            return ""
        try:
            raw = self.backend.chat(self.EXTRACT_PROMPT, user_input).strip()
            if raw.upper().startswith("NONE"):
                return ""
            import json as _json
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1:
                return ""
            cand = _json.loads(raw[start:end + 1])
            kw = str(cand.get("keyword", "")).strip()
            fact = str(cand.get("fact", "")).strip()
            if not kw or not fact:
                return ""
            if self.store.find_similar(kw, fact, threshold=0.5):
                return ""     # already known
            quoted = f'"{kw}"' if " " in kw else kw
            return (f"\n\n[memory suggestion] Worth remembering? "
                    f"Type:  /save {quoted} {fact}")
        except Exception:
            return ""         # extraction must never break the chat

    def _system_with_memory(self, user_input: str) -> str:
        ctx = self.store.build_context(
            query=user_input, top_k=self.top_k,
            max_context_tokens=self.max_context_tokens)
        return self.system_prompt + ("\n\n" + ctx if ctx else "")

    def ask(self, user_input: str) -> str:
        cmd = self.handle_command(user_input)
        if cmd is not None:
            return cmd
        system = self._system_with_memory(user_input)
        if self.backend is None:
            return f"(no backend)\n--- system prompt ---\n{system}"
        reply = self.backend.chat(system, user_input, list(self.history))
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": reply})
        return reply + self._suggest_memory(user_input)

    def ask_stream(self, user_input: str):
        """Yields chunks. Commands yield a single chunk."""
        cmd = self.handle_command(user_input)
        if cmd is not None:
            yield cmd
            return
        system = self._system_with_memory(user_input)
        if self.backend is None:
            yield f"(no backend)\n--- system prompt ---\n{system}"
            return
        pieces = []
        for chunk in self.backend.chat_stream(system, user_input,
                                              list(self.history)):
            pieces.append(chunk)
            yield chunk
        reply = "".join(pieces)
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": reply})
        suggestion = self._suggest_memory(user_input)
        if suggestion:
            yield suggestion

    def repl(self, stream: bool = True) -> None:
        print("memlayer — /help for commands, 'quit' to exit.\n")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in ("quit", "exit"):
                break
            if not user_input:
                continue
            print("Assistant: ", end="", flush=True)
            try:
                if stream:
                    for chunk in self.ask_stream(user_input):
                        print(chunk, end="", flush=True)
                    print("\n")
                else:
                    print(self.ask(user_input) + "\n")
            except ConnectionError as e:
                print(f"\n[backend error] {e}\n")
