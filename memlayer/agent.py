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
    "  /task add <name> <kw> <url>   — define a URL-fetch task\n"
    "  /task run|list|remove ...     — run tasks; re-runs supersede old data\n"
    "  /blocked                      — list prohibited terms\n"
    "  /profile <name>               — switch memory profile\n"
    "  /enable [password]  /lock     — secure mode unlock / lock\n"
    "  /passwd [current new]         — change the password\n"
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
        self._tasks = None

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

    MEMORY_CMDS = frozenset({
        "/save", "/save!", "/replace", "/undo", "/search", "/memories",
        "/list", "/forget", "/clear", "/stats", "/export", "/import",
        "/block", "/unblock", "/blocked", "/history", "/task"})
    ALL_CMDS = MEMORY_CMDS | {"/enable", "/passwd", "/lock", "/profile",
                              "/auto", "/help"}

    def handle_command(self, text: str) -> Optional[str]:
        t = text.strip()
        if not t.startswith("/"):
            return None
        # exact first-token matching: "/clearance levels" is NOT /clear
        cmd, _, rest = t.partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()
        if cmd not in self.ALL_CMDS:
            return (f"Unknown command {cmd}. Type /help for the list. "
                    f"(Messages starting with / are treated as commands.)")
        sec = self.store.security

        if cmd == "/enable":
            password = rest
            if not password and sys.stdin.isatty():
                import getpass
                password = getpass.getpass("Password (hidden): ")
            if not password:
                return "Usage: /enable <password>"
            return sec.enable(password)

        if cmd == "/passwd":
            parts = rest.split()
            if len(parts) == 2:
                old, new = parts
            elif not parts and sys.stdin.isatty():
                import getpass
                old = getpass.getpass("Current password (hidden): ")
                new = getpass.getpass("New password (hidden): ")
                confirm = getpass.getpass("Repeat new password (hidden): ")
                if new != confirm:
                    return "New passwords do not match. Password unchanged."
            else:
                return ("Usage: /passwd <current> <new>\n"
                        "(or just /passwd in a terminal for hidden prompts)")
            return sec.change_password(old, new)

        if cmd == "/lock":
            return sec.lock()

        if cmd == "/profile":
            if not rest:
                return f"Current profile: {self.store.user_id}"
            self.store.switch_user(rest)
            self.history.clear()
            new_sec = self.store.security
            state = " (locked)" if (new_sec.secure and
                                    not new_sec.check()) else ""
            return f"Switched to profile '{rest}'{state}."

        if cmd == "/auto":
            arg = rest.lower()
            if arg == "on":
                self.auto_extract = True
                return ("Auto-extraction ON: after each reply I will suggest "
                        "facts worth saving. Nothing is saved without you.")
            if arg == "off":
                self.auto_extract = False
                return "Auto-extraction OFF."
            return (f"Auto-extraction is "
                    f"{'ON' if self.auto_extract else 'OFF'}. Use /auto on|off.")

        if cmd == "/help":
            return HELP

        # every remaining command touches memory -> lock check
        if not sec.check():
            return LOCKED_MSG

        if cmd == "/task":
            from .tasks import TaskManager
            if self._tasks is None:
                self._tasks = TaskManager(self.store)
            parts = rest.split(maxsplit=1)
            sub = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""
            if sub == "add":
                p = arg.split(maxsplit=2)
                if len(p) < 3:
                    return ("Usage: /task add <name> <keyword> <url>\n"
                            "e.g.  /task add news headlines https://example.com")
                return self._tasks.add(p[0], p[1], p[2])
            if sub == "run":
                if not arg:
                    return "Usage: /task run <name>"
                return self._tasks.run(arg)
            if sub == "list":
                return self._tasks.list()
            if sub == "remove":
                if not arg:
                    return "Usage: /task remove <name>"
                return self._tasks.remove(arg)
            return ("Usage: /task add <name> <keyword> <url> | "
                    "/task run <name> | /task list | /task remove <name>")

        if cmd == "/save!":
            return self._do_save(rest, force=True)
        if cmd == "/save":
            return self._do_save(rest, force=False)

        if cmd == "/replace":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /replace <id> <keyword> <fact>"
            return self._do_save(parts[1], force=True, supersedes=parts[0])

        if cmd == "/history":
            kw = rest.strip('"').strip("'")
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

        if cmd == "/undo":
            if not self._last_saved_id:
                return "Nothing to undo."
            ok = self.store.forget(self._last_saved_id)
            mid, self._last_saved_id = self._last_saved_id, None
            return f"Removed [{mid}]." if ok else "Already removed."

        if cmd == "/search":
            if not rest:
                return "Usage: /search <query>"
            hits = self.store.search(rest, top_k=5)
            if not hits:
                return "No matching memories."
            return "Matches:\n" + "\n".join(
                f"[{m['id']}] ({m['keyword']}) {m['text']}" for m in hits)

        if cmd in ("/memories", "/list"):
            mems = self.store.all()
            if not mems:
                return "No memories saved yet."
            return "Saved memories:\n" + "\n".join(
                f"[{m['id']}] ({m['keyword']}) {m['text']}  ({m['created_at']})"
                for m in mems)

        if cmd == "/forget":
            if not rest:
                return "Usage: /forget <memory id>"
            return (f"Forgot memory {rest}." if self.store.forget(rest)
                    else f"No memory found with id {rest}.")

        if cmd == "/clear":
            return f"Cleared {self.store.clear()} memories."

        if cmd == "/stats":
            s = self.store.stats()
            kws = ", ".join(f"{k}: {v}" for k, v in
                            sorted(s["by_keyword"].items())) or "-"
            return (f"Profile: {self.store.user_id}\n"
                    f"Memories: {s['count']}\nBy keyword: {kws}\n"
                    f"DB size: {s['db_bytes']/1024:.1f} KB\n"
                    f"Oldest: {s['oldest']}  Newest: {s['newest']}")

        if cmd == "/export":
            fname = rest or "memlayer_export.json"
            Path(fname).write_text(self.store.export_json(), encoding="utf-8")
            return f"Exported {self.store.count()} memories to {fname}"

        if cmd == "/import":
            if not rest or not Path(rest).exists():
                return "Usage: /import <file.json>  (file must exist)"
            try:
                n = self.store.import_json(
                    Path(rest).read_text(encoding="utf-8"))
            except ValueError as e:
                return f"Import rejected: {e}"
            return f"Imported {n} memories."

        if cmd == "/blocked":
            terms = self.store.list_blocked()
            return ("Prohibited terms: " + ", ".join(terms)) if terms \
                else "No prohibited terms set. Add one with /block <term>."

        if cmd == "/block":
            if not rest:
                return "Usage: /block <term>"
            self.store.add_blocked(rest)
            return (f'Added prohibited term "{rest.lower()}". '
                    f"Saves containing it will be refused.")

        if cmd == "/unblock":
            if not rest:
                return "Usage: /unblock <term>"
            return (f'Removed "{rest.lower()}" from prohibited terms.'
                    if self.store.remove_blocked(rest)
                    else f'"{rest.lower()}" was not in the prohibited list.')

        return None

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
