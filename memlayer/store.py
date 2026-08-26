"""
memlayer.store — Fast persistent memory engine.

Storage:   SQLite (single file, WAL mode)
Search:    FTS5 + BM25 (microsecond-fast, zero dependencies)
Optional:  semantic reranking (sentence-transformers), guardrails, expiry
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

_WORD = re.compile(r"[a-zA-Z0-9\u0600-\u06FF\u4e00-\u9fff]+")


def _fts_query(text: str) -> str:
    tokens = _WORD.findall(text.lower())
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else ""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def parse_duration(spec: str) -> Optional[int]:
    """'30d' | '12h' | '2w' | '45m' -> seconds, or None if invalid."""
    m = re.fullmatch(r"(\d+)([mhdw])", spec.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


class MemoryStore:
    """Persistent, per-user memory backed by SQLite + FTS5."""

    def __init__(self, path: str = "memlayer.db", user_id: str = "default",
                 secure: bool = False, guardrails=None,
                 use_embeddings: bool = False,
                 embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.path = str(Path(path))
        self.user_id = user_id
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

        from .security import SecurityManager
        self._secure_flag = secure
        self.security = SecurityManager(self._db, user_id, secure)

        self.guardrails = guardrails  # Guardrails instance or None
        self._init_blocklist_schema()
        if self.guardrails is not None:
            # let guardrails see the persistent /block list too
            self.guardrails.extra_blocklist_provider = self.list_blocked

        self._embedder = None
        self._embedding_model = embedding_model
        self._want_embeddings = use_embeddings  # lazy-loaded on first use

    def _get_embedder(self):
        if self._want_embeddings and self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(self._embedding_model)
            except ImportError:
                print("[memlayer] sentence-transformers not installed; BM25 only.")
                self._want_embeddings = False
        return self._embedder

    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            text       TEXT NOT NULL,
            tags       TEXT NOT NULL DEFAULT '[]',
            embedding  BLOB,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
            text, tags, content='memories', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
            INSERT INTO mem_fts(rowid, text, tags)
            VALUES (new.rowid, new.text, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
            INSERT INTO mem_fts(mem_fts, rowid, text, tags)
            VALUES ('delete', old.rowid, old.text, old.tags);
        END;
        """)
        # migrate old DBs that lack expires_at
        cols = [r[1] for r in self._db.execute("PRAGMA table_info(memories)")]
        if "expires_at" not in cols:
            self._db.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
        self._db.commit()

    # ------------------------------------------------------------------
    # persistent prohibited-terms blocklist
    # ------------------------------------------------------------------

    def _init_blocklist_schema(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS blocklist (
                user_id TEXT NOT NULL,
                term    TEXT NOT NULL,
                PRIMARY KEY (user_id, term)
            )""")
        self._db.commit()

    def add_blocked(self, term: str) -> bool:
        term = term.strip().lower()
        if not term:
            return False
        self._db.execute(
            "INSERT OR IGNORE INTO blocklist (user_id, term) VALUES (?, ?)",
            (self.user_id, term))
        self._db.commit()
        return True

    def remove_blocked(self, term: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM blocklist WHERE user_id=? AND term=?",
            (self.user_id, term.strip().lower()))
        self._db.commit()
        return cur.rowcount > 0

    def list_blocked(self) -> list[str]:
        return [r[0] for r in self._db.execute(
            "SELECT term FROM blocklist WHERE user_id=? ORDER BY term",
            (self.user_id,))]

    # ------------------------------------------------------------------
    # profiles
    # ------------------------------------------------------------------

    def switch_user(self, user_id: str) -> None:
        """Switch to another profile. Secure profiles start locked."""
        from .security import SecurityManager
        self.user_id = user_id
        self.security = SecurityManager(self._db, user_id, self._secure_flag)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, text: str, keyword: str = "general",
             tags: Optional[list[str]] = None,
             expires_in: Optional[int] = None) -> dict:
        if not self.security.check():
            raise PermissionError(
                "Memory is locked (secure mode). Unlock with /enable <password>.")
        text = text.strip()
        if not text:
            raise ValueError("Cannot save empty memory.")

        guard_warnings: list[str] = []
        if self.guardrails is not None:
            res = self.guardrails.on_save(text)
            if not res.allowed:
                raise ValueError("Guardrail: " + " ".join(res.warnings))
            text = res.text
            guard_warnings = res.warnings
        else:
            # persistent /block list applies even without a Guardrails object
            low = text.lower()
            hit = next((w for w in self.list_blocked() if w in low), None)
            if hit:
                raise ValueError(
                    f'Guardrail: Blocked: contains prohibited term "{hit}".')

        keyword = keyword.strip().lower() or "general"
        tags = [keyword] + (tags or [])

        emb_blob = None
        embedder = self._get_embedder()
        if embedder is not None:
            import numpy as np
            vec = embedder.encode([text], normalize_embeddings=True)[0]
            emb_blob = np.asarray(vec, dtype="float32").tobytes()

        expires_at = None
        if expires_in:
            expires_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + expires_in))

        record = {"id": uuid.uuid4().hex[:8], "text": text, "keyword": keyword,
                  "tags": tags, "created_at": _now(), "expires_at": expires_at,
                  "warnings": guard_warnings}
        try:
            self._db.execute(
                "INSERT INTO memories (id,user_id,text,tags,embedding,"
                "created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                (record["id"], self.user_id, text, json.dumps(tags),
                 emb_blob, record["created_at"], expires_at))
            self._db.commit()
        except sqlite3.Error:
            self._db.rollback()
            raise
        return record

    def find_similar(self, keyword: str, text: str,
                     threshold: float = 0.6) -> Optional[dict]:
        """Find an existing memory with same keyword and high word overlap."""
        new_tokens = set(_WORD.findall(text.lower()))
        if not new_tokens:
            return None
        for m in self.all():
            if m["keyword"] != keyword.strip().lower():
                continue
            old_tokens = set(_WORD.findall(m["text"].lower()))
            if not old_tokens:
                continue
            overlap = len(new_tokens & old_tokens) / min(len(new_tokens),
                                                         len(old_tokens))
            if overlap >= threshold:
                return m
        return None

    def forget(self, memory_id: str) -> bool:
        try:
            cur = self._db.execute(
                "DELETE FROM memories WHERE id=? AND user_id=?",
                (memory_id, self.user_id))
            self._db.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            self._db.rollback()
            raise

    def clear(self) -> int:
        cur = self._db.execute("DELETE FROM memories WHERE user_id=?",
                               (self.user_id,))
        self._db.commit()
        return cur.rowcount

    def _row_to_dict(self, r) -> dict:
        tags = json.loads(r[2])
        return {"id": r[0], "text": r[1], "tags": tags,
                "keyword": tags[0] if tags else "general",
                "created_at": r[3], "expires_at": r[4]}

    def all(self, include_expired: bool = False) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,text,tags,created_at,expires_at FROM memories "
            "WHERE user_id=? ORDER BY created_at", (self.user_id,)).fetchall()
        mems = [self._row_to_dict(r) for r in rows]
        if include_expired:
            return mems
        now = _now()
        return [m for m in mems if not m["expires_at"] or m["expires_at"] > now]

    def purge_expired(self) -> int:
        cur = self._db.execute(
            "DELETE FROM memories WHERE user_id=? AND expires_at IS NOT NULL "
            "AND expires_at <= ?", (self.user_id, _now()))
        self._db.commit()
        return cur.rowcount

    def count(self) -> int:
        return len(self.all())

    def stats(self) -> dict:
        mems = self.all()
        by_kw: dict[str, int] = {}
        for m in mems:
            by_kw[m["keyword"]] = by_kw.get(m["keyword"], 0) + 1
        size = Path(self.path).stat().st_size if Path(self.path).exists() else 0
        return {"count": len(mems), "by_keyword": by_kw,
                "db_bytes": size,
                "oldest": mems[0]["created_at"] if mems else None,
                "newest": mems[-1]["created_at"] if mems else None}

    def export_json(self) -> str:
        return json.dumps(self.all(include_expired=True),
                          ensure_ascii=False, indent=2)

    def import_json(self, data: str) -> int:
        items = json.loads(data)
        n = 0
        for it in items:
            self.save(it["text"], keyword=it.get("keyword", "general"))
            n += 1
        return n

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        self.purge_expired()
        q = _fts_query(query)
        if not q:
            return self.all()[-top_k:]
        rows = self._db.execute(
            """SELECT m.id,m.text,m.tags,m.created_at,m.expires_at,m.embedding,
                      bm25(mem_fts) AS score
               FROM mem_fts JOIN memories m ON m.rowid = mem_fts.rowid
               WHERE mem_fts MATCH ? AND m.user_id=?
               ORDER BY score LIMIT ?""",
            (q, self.user_id, max(top_k * 4, top_k))).fetchall()
        results = []
        for r in rows:
            d = self._row_to_dict(r[:5])
            d["_emb"] = r[5]
            results.append(d)

        embedder = self._get_embedder()
        if embedder is not None and results:
            import numpy as np
            qv = embedder.encode([query], normalize_embeddings=True)[0]
            for m in results:
                m["_sim"] = (float(np.dot(qv, np.frombuffer(m["_emb"], "float32")))
                             if m["_emb"] else 0.0)
            results.sort(key=lambda m: m["_sim"], reverse=True)

        for m in results:
            m.pop("_emb", None); m.pop("_sim", None)
        return results[:top_k]

    # ------------------------------------------------------------------
    # context injection (with token budget + injection shield)
    # ------------------------------------------------------------------

    def build_context(self, query: str = "", top_k: int = 5,
                      include_all_if_few: int = 12,
                      max_context_tokens: Optional[int] = None) -> str:
        if not self.security.check():
            return ""
        self.purge_expired()
        if self.count() == 0:
            return ""
        if self.count() <= include_all_if_few or not query:
            selected = self.all()
        else:
            selected = self.search(query, top_k=top_k)

        if self.guardrails is not None:
            selected, _ = self.guardrails.on_inject(selected)
        if not selected:
            return ""

        if max_context_tokens:
            budget, kept = max_context_tokens, []
            for m in selected:
                cost = max(1, len(m["text"]) // 4)  # ~4 chars per token
                if cost > budget:
                    break
                kept.append(m); budget -= cost
            selected = kept or selected[:1]

        lines = "\n".join(f"- {m['text']}" for m in selected)
        return ("Known facts about the user (saved memory — use naturally, "
                "never mention this list):\n" + lines)

    def close(self) -> None:
        self._db.close()
