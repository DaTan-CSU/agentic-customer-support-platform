"""Local SQLite-backed trace processor for the Agents SDK.

Mirrors `.agent_sessions.db` style: one file beside server.py, two tables
(`traces`, `spans`). Tracing in main.py is enabled and the default exporter is
replaced with `SQLiteTraceProcessor` so spans never leave this machine.

Why not OTel: this is a demo, and the existing UI just needs a span tree per
thread. Keeping everything in one SQLite file matches `.agent_sessions.db`
ergonomics and avoids requiring a collector to be running.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span
from agents.tracing.traces import Trace

TRACE_DB_PATH = Path(__file__).with_name(".agent_traces.db")


def _thread_id_from_metadata(meta: Any) -> str | None:
    """Pull our injected `thread_id` from a Trace's metadata dict, if present."""
    if isinstance(meta, dict):
        v = meta.get("thread_id")
        if isinstance(v, str):
            return v
    return None


class SQLiteTraceProcessor(TracingProcessor):
    """Append-only trace processor.

    on_span_end / on_trace_end fire after the SDK has populated timing and
    error info, so the row is final at insert time. We don't update partial
    rows — keeps the schema simple, idempotency is by (trace_id, span_id).
    """

    def __init__(self, db_path: Path | str = TRACE_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        # cache trace_id → thread_id resolved from metadata, so spans can be
        # tagged with the same thread_id even though Span itself only carries
        # trace_metadata for the top-level Trace.
        self._trace_thread: dict[str, str] = {}

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    workflow_name TEXT,
                    thread_id TEXT,
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    ended_at TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_traces_thread ON traces(thread_id);

                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT,
                    trace_id TEXT,
                    parent_id TEXT,
                    kind TEXT,
                    name TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    error_json TEXT,
                    data_json TEXT,
                    PRIMARY KEY (trace_id, span_id)
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
                """
            )

    # -- TracingProcessor API ------------------------------------------------

    def on_trace_start(self, trace: Trace) -> None:
        # Persist immediately so a long-running trace is queryable mid-flight.
        try:
            exported = trace.export() or {}
        except Exception:
            exported = {}
        meta = exported.get("metadata") if isinstance(exported, dict) else None
        thread_id = _thread_id_from_metadata(meta)
        if thread_id:
            self._trace_thread[trace.trace_id] = thread_id
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO traces(trace_id, workflow_name, thread_id, metadata_json) VALUES (?,?,?,?)",
                (
                    trace.trace_id,
                    getattr(trace, "name", None),
                    thread_id,
                    json.dumps(meta, default=str) if meta is not None else None,
                ),
            )

    def on_trace_end(self, trace: Trace) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE traces SET ended_at = CURRENT_TIMESTAMP WHERE trace_id = ?",
                (trace.trace_id,),
            )
        self._trace_thread.pop(trace.trace_id, None)

    def on_span_start(self, span: Span[Any]) -> None:
        # We write on span_end; tracking start here gives no extra signal and
        # would double the I/O. Kept as no-op intentionally.
        return

    def on_span_end(self, span: Span[Any]) -> None:
        try:
            exported = span.export() or {}
        except Exception:
            exported = {}
        sd = exported.get("span_data") if isinstance(exported, dict) else None
        kind = sd.get("type") if isinstance(sd, dict) else None
        name = sd.get("name") if isinstance(sd, dict) else None
        err = exported.get("error") if isinstance(exported, dict) else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO spans
                    (span_id, trace_id, parent_id, kind, name, started_at, ended_at, error_json, data_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_id,
                    kind,
                    name,
                    span.started_at,
                    span.ended_at,
                    json.dumps(err, default=str) if err else None,
                    json.dumps(sd, default=str) if sd else None,
                ),
            )

    def shutdown(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def force_flush(self) -> None:
        with self._lock, self._conn:
            self._conn.commit()


# -- Query helpers used by the /traces endpoint -----------------------------


def load_traces_for_thread(thread_id: str, db_path: Path | str = TRACE_DB_PATH) -> list[dict]:
    """Return traces (with nested spans) attached to a thread, newest first."""
    p = Path(db_path)
    if not p.exists():
        return []
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    try:
        traces = con.execute(
            "SELECT trace_id, workflow_name, thread_id, started_at, ended_at "
            "FROM traces WHERE thread_id = ? ORDER BY started_at DESC",
            (thread_id,),
        ).fetchall()
        out = []
        for t in traces:
            spans = con.execute(
                "SELECT span_id, parent_id, kind, name, started_at, ended_at, error_json, data_json "
                "FROM spans WHERE trace_id = ? ORDER BY started_at",
                (t["trace_id"],),
            ).fetchall()
            out.append(
                {
                    "trace_id": t["trace_id"],
                    "workflow_name": t["workflow_name"],
                    "thread_id": t["thread_id"],
                    "started_at": t["started_at"],
                    "ended_at": t["ended_at"],
                    "spans": [
                        {
                            "span_id": s["span_id"],
                            "parent_id": s["parent_id"],
                            "kind": s["kind"],
                            "name": s["name"],
                            "started_at": s["started_at"],
                            "ended_at": s["ended_at"],
                            "error": json.loads(s["error_json"]) if s["error_json"] else None,
                            "data": json.loads(s["data_json"]) if s["data_json"] else None,
                        }
                        for s in spans
                    ],
                }
            )
        return out
    finally:
        con.close()
