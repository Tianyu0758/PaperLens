# 文件说明：使用 SQLite 持久化会话、轮次、原生消息、任务事件和文件记录。
# 集中处理请求去重、同会话运行约束、历史分页、重启恢复及文件归属与路径校验。

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import BaseMessage, HumanMessage, messages_from_dict, messages_to_dict

ACTIVE = ("queued", "running")
TERMINAL = ("completed", "failed", "interrupted")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class StoreError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class AgentStore:
    """Short transactions; every operation owns and closes its connection."""

    def __init__(self, path: Path, data_dir: Path):
        self.path = Path(path)
        self.data_dir = Path(data_dir).resolve()

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    paper_id TEXT,
                    context_summary TEXT NOT NULL DEFAULT '',
                    summary_through_turn INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    request_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'agent',
                    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','interrupted')),
                    stage TEXT NOT NULL,
                    error TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    retry_of TEXT REFERENCES runs(run_id),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(session_id, request_id),
                    UNIQUE(session_id, turn_index)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_session
                    ON runs(session_id) WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence_index INTEGER NOT NULL,
                    scope TEXT NOT NULL,
                    parent_call_id TEXT,
                    native_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    visible INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, scope, native_id),
                    UNIQUE(run_id, sequence_index)
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    filename TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id, updated_at);
                CREATE INDEX IF NOT EXISTS messages_by_session ON messages(session_id, run_id, sequence_index);
                CREATE INDEX IF NOT EXISTS runs_by_session ON runs(session_id, turn_index);
                CREATE INDEX IF NOT EXISTS artifacts_by_session ON artifacts(session_id, created_at);
            """)
            # Existing 0.2 databases retain all sessions, messages and artifacts.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            if "mode" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'agent'")

    @staticmethod
    def _session(db: sqlite3.Connection, session_id: str, user_id: str) -> dict:
        row = db.execute(
            "SELECT * FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id)
        ).fetchone()
        if row is None:
            raise StoreError(404, "会话不存在")
        return dict(row)

    def get_session(self, session_id: str, user_id: str) -> dict:
        with self.connect() as db:
            return self._session(db, session_id, user_id)

    def create_session(self, user_id: str, paper_id: str, title: str = "新对话") -> dict:
        now = utc_now()
        session_id = new_id("s")
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO sessions(session_id,user_id,title,paper_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (session_id, user_id, title, paper_id, now, now),
            )
            return self._session(db, session_id, user_id)

    def list_sessions(self, user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT s.session_id,s.title,s.paper_id,s.created_at,s.updated_at,
                    (SELECT run_id FROM runs r WHERE r.session_id=s.session_id ORDER BY turn_index DESC LIMIT 1) AS latest_run_id,
                    (SELECT status FROM runs r WHERE r.session_id=s.session_id ORDER BY turn_index DESC LIMIT 1) AS latest_run_status
                FROM sessions s WHERE user_id=? ORDER BY updated_at DESC,session_id LIMIT ? OFFSET ?
            """, (user_id, limit, offset)).fetchall()
            return [dict(row) for row in rows]

    def rename_session(self, session_id: str, user_id: str, title: str) -> dict:
        with self.connect(write=True) as db:
            self._session(db, session_id, user_id)
            db.execute("UPDATE sessions SET title=?,updated_at=? WHERE session_id=?", (title, utc_now(), session_id))
            return self._session(db, session_id, user_id)

    @staticmethod
    def _event(db: sqlite3.Connection, run_id: str, event_type: str, payload: dict) -> dict:
        seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
        now = utc_now()
        db.execute("INSERT INTO run_events VALUES(?,?,?,?,?)", (run_id, seq, event_type, json.dumps(payload, ensure_ascii=False), now))
        return {"run_id": run_id, "seq": seq, "event": event_type, "data": payload, "created_at": now}

    @staticmethod
    def _message(db: sqlite3.Connection, run: dict, message: BaseMessage, *, scope: str = "main",
                 parent_call_id: str | None = None, visible: bool = False) -> str:
        if not message.id:
            message.id = f"tool_{message.tool_call_id}" if message.type == "tool" else new_id("native")
        existing = db.execute("SELECT message_id FROM messages WHERE run_id=? AND scope=? AND native_id=?",
                              (run["run_id"], scope, message.id)).fetchone()
        if existing:
            if visible:
                db.execute("UPDATE messages SET visible=1 WHERE message_id=?", (existing[0],))
            return existing[0]
        message_id = new_id("m")
        sequence = db.execute("SELECT COALESCE(MAX(sequence_index),-1)+1 FROM messages WHERE run_id=?", (run["run_id"],)).fetchone()[0]
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            message_id, run["session_id"], run["run_id"], sequence, scope, parent_call_id, message.id,
            message.type, json.dumps(messages_to_dict([message])[0], ensure_ascii=False), int(visible), utc_now(),
        ))
        return message_id

    def create_run(self, session_id: str, user_id: str, question: str, request_id: str,
                   *, retry_of: str | None = None, mode: str = "agent") -> tuple[dict, bool]:
        if mode not in ("agent", "qa"):
            raise StoreError(400, "未知任务模式")
        with self.connect(write=True) as db:
            session = self._session(db, session_id, user_id)
            row = db.execute("SELECT * FROM runs WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if row:
                if row["question"] != question or row["retry_of"] != retry_of or row["mode"] != mode:
                    raise StoreError(409, "同一 request_id 不能用于不同请求")
                return dict(row), False
            if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('queued','running')", (session_id,)).fetchone():
                raise StoreError(409, "该会话已有正在执行的任务")
            turn = db.execute("SELECT COALESCE(MAX(turn_index),0)+1 FROM runs WHERE session_id=?", (session_id,)).fetchone()[0]
            run_id, now = new_id("run"), utc_now()
            db.execute("""INSERT INTO runs(run_id,session_id,request_id,turn_index,question,mode,status,stage,retry_of,created_at)
                          VALUES(?,?,?,?,?,?,'queued','queued',?,?)""", (run_id, session_id, request_id, turn, question, mode, retry_of, now))
            run = dict(db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
            self._message(db, run, HumanMessage(content=question, id=new_id("human")), visible=True)
            title = question[:30] if session["title"] == "新对话" else session["title"]
            db.execute("UPDATE sessions SET title=?,updated_at=? WHERE session_id=?", (title, now, session_id))
            self._event(db, run_id, "run.queued", {"status": "queued", "stage": "queued", "session_id": session_id})
            return run, True

    @staticmethod
    def _run(db: sqlite3.Connection, run_id: str) -> dict:
        row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(404, "任务不存在")
        return dict(row)

    def get_run(self, run_id: str, user_id: str) -> dict:
        with self.connect() as db:
            run = self._run(db, run_id)
            session = db.execute("SELECT * FROM sessions WHERE session_id=?", (run["session_id"],)).fetchone()
            if session["user_id"] != user_id:
                raise StoreError(404, "任务不存在")
            result = json.loads(run.pop("result_json"))
            run.update(result, paper_id=session["paper_id"])
            run["last_event_seq"] = db.execute("SELECT COALESCE(MAX(seq),0) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
            return run

    def list_runs(self, session_id: str, user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connect() as db:
            self._session(db, session_id, user_id)
            rows = db.execute("""SELECT run_id,session_id,turn_index,request_id,question,mode,status,stage,error,
                retry_of,created_at,started_at,finished_at FROM runs WHERE session_id=? ORDER BY turn_index DESC LIMIT ? OFFSET ?""",
                (session_id, limit, offset)).fetchall()
            return [dict(row) for row in rows]

    def start_run(self, run_id: str) -> bool:
        with self.connect(write=True) as db:
            changed = db.execute("UPDATE runs SET status='running',started_at=? WHERE run_id=? AND status='queued'", (utc_now(), run_id)).rowcount
            if changed:
                self._event(db, run_id, "run.started", {"status": "running"})
            return bool(changed)

    def set_stage(self, run_id: str, stage: str, details: dict | None = None) -> None:
        with self.connect(write=True) as db:
            if not db.execute("UPDATE runs SET stage=? WHERE run_id=? AND status='running'", (stage, run_id)).rowcount:
                raise StoreError(409, "任务已结束")
            self._event(db, run_id, "stage.changed", {**(details or {}), "stage": stage})


    def append_event(self, run_id: str, event_type: str, payload: dict) -> dict:
        with self.connect(write=True) as db:
            run = self._run(db, run_id)
            if run["status"] not in ACTIVE:
                raise StoreError(409, "任务已结束")
            return self._event(db, run_id, event_type, payload)

    def events(self, run_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, after, limit)).fetchall()
            return [{"run_id": row["run_id"], "seq": row["seq"], "event": row["event_type"],
                     "data": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows]

    def record_message(self, run_id: str, message: BaseMessage, *, scope: str = "main", parent_call_id: str | None = None) -> None:
        with self.connect(write=True) as db:
            run = self._run(db, run_id)
            if run["status"] not in ACTIVE:
                raise StoreError(409, "任务已结束")
            self._message(db, run, message, scope=scope, parent_call_id=parent_call_id)

    def user_message(self, run_id: str) -> HumanMessage:
        with self.connect() as db:
            row = db.execute("SELECT message_json FROM messages WHERE run_id=? AND scope='main' AND role='human' ORDER BY sequence_index LIMIT 1", (run_id,)).fetchone()
            if row is None:
                raise StoreError(404, "任务用户消息不存在")
            return messages_from_dict([json.loads(row[0])])[0]

    def completed_turns(self, session_id: str, *, after_turn: int = 0) -> list[tuple[int, list[BaseMessage]]]:
        with self.connect() as db:
            rows = db.execute("""SELECT r.turn_index,m.message_json FROM messages m JOIN runs r ON r.run_id=m.run_id
                WHERE r.session_id=? AND r.status='completed' AND r.turn_index>? AND m.scope='main'
                ORDER BY r.turn_index,m.sequence_index""", (session_id, after_turn)).fetchall()
            turns: dict[int, list] = {}
            for row in rows:
                turns.setdefault(row[0], []).append(json.loads(row[1]))
            return [(turn, messages_from_dict(messages)) for turn, messages in turns.items()]

    def save_summary(self, session_id: str, summary: str, through_turn: int) -> None:
        with self.connect(write=True) as db:
            db.execute("UPDATE sessions SET context_summary=?,summary_through_turn=? WHERE session_id=? AND summary_through_turn<?",
                       (summary, through_turn, session_id, through_turn))

    def visible_messages(self, session_id: str, user_id: str, limit: int = 100, before: int | None = None) -> list[dict]:
        with self.connect() as db:
            self._session(db, session_id, user_id)
            rows = db.execute("""SELECT m.*,r.turn_index,r.status AS run_status,r.error,r.result_json FROM messages m
                JOIN runs r ON r.run_id=m.run_id WHERE m.session_id=? AND m.visible=1 AND r.turn_index IN (
                    SELECT turn_index FROM runs WHERE session_id=? AND (? IS NULL OR turn_index<?)
                    ORDER BY turn_index DESC LIMIT ?)
                ORDER BY r.turn_index DESC,m.sequence_index DESC""", (session_id, session_id, before, before, limit)).fetchall()
            items = []
            for row in reversed(rows):
                message = messages_from_dict([json.loads(row["message_json"])])[0]
                item = {"message_id": row["message_id"], "run_id": row["run_id"], "turn_index": row["turn_index"],
                        "role": "user" if row["role"] == "human" else "assistant", "content": message.content,
                        "created_at": row["created_at"], "run_status": row["run_status"], "error": row["error"]}
                if item["role"] == "assistant":
                    result = json.loads(row["result_json"])
                    item["citations"] = result.get("citations", [])
                    item["artifacts"] = result.get("artifact_records", [])
                items.append(item)
            return items

    def complete_run(self, run_id: str, result: dict, messages: list[BaseMessage]) -> None:
        with self.connect(write=True) as db:
            run = self._run(db, run_id)
            if run["status"] != "running":
                raise StoreError(409, "任务已结束")
            for index, message in enumerate(messages):
                self._message(db, run, message, visible=index == len(messages) - 1 and message.type == "ai" and not getattr(message, "tool_calls", []))
            now = utc_now()
            db.execute("UPDATE runs SET status='completed',stage='done',result_json=?,finished_at=? WHERE run_id=?",
                       (json.dumps(result, ensure_ascii=False), now, run_id))
            db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, run["session_id"]))
            self._event(db, run_id, "run.completed", {"status": "completed", "stage": "done", "result_url": f"/runs/{run_id}"})

    def end_run(self, run_id: str, status: str, error: str, timings: dict | None = None) -> None:
        if status not in ("failed", "interrupted"):
            raise ValueError(status)
        with self.connect(write=True) as db:
            run = self._run(db, run_id)
            if run["status"] not in ACTIVE:
                return
            stage = "error" if status == "failed" else "interrupted"
            db.execute("UPDATE runs SET status=?,stage=?,error=?,result_json=?,finished_at=? WHERE run_id=?",
                       (status, stage, error, json.dumps({"timings": timings or {}}), utc_now(), run_id))
            self._event(db, run_id, f"run.{status}", {"status": status, "stage": stage, "error": error})

    def recover_interrupted(self) -> int:
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT run_id FROM runs WHERE status IN ('queued','running')")]
        for run_id in ids:
            self.end_run(run_id, "interrupted", "后端进程已重启，任务中断，可重新提交或重试")
        return len(ids)

    def register_artifact(self, run_id: str, relative_path: str, media_type: str) -> tuple[dict, bool]:
        path = self.resolve_run_file(run_id, relative_path)
        with self.connect(write=True) as db:
            run = self._run(db, run_id)
            if run["status"] not in ACTIVE:
                raise StoreError(409, "任务已结束")
            row = db.execute("SELECT * FROM artifacts WHERE run_id=? AND relative_path=?", (run_id, relative_path)).fetchone()
            if row:
                return self._public_artifact(dict(row)), False
            artifact = {"artifact_id": new_id("a"), "session_id": run["session_id"], "run_id": run_id,
                        "filename": path.name, "relative_path": relative_path, "media_type": media_type,
                        "size_bytes": path.stat().st_size, "created_at": utc_now()}
            db.execute("INSERT INTO artifacts VALUES(:artifact_id,:session_id,:run_id,:filename,:relative_path,:media_type,:size_bytes,:created_at)", artifact)
            public = self._public_artifact(artifact)
            self._event(db, run_id, "artifact.created", public)
            return public, True

    @staticmethod
    def _public_artifact(artifact: dict) -> dict:
        return {key: value for key, value in artifact.items() if key != "relative_path"} | {"download_url": f"/artifacts/{artifact['artifact_id']}"}

    def resolve_run_file(self, run_id: str, relative_path: str) -> Path:
        root = (self.data_dir / "runs" / run_id).resolve()
        if not root.is_relative_to((self.data_dir / "runs").resolve()) or root.name != run_id:
            raise StoreError(400, "非法任务路径")
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise StoreError(404, "文件不存在")
        return path

    def artifact(self, artifact_id: str, user_id: str, *, session_id: str | None = None) -> tuple[dict, Path]:
        with self.connect() as db:
            row = db.execute("SELECT a.* FROM artifacts a JOIN sessions s ON s.session_id=a.session_id WHERE artifact_id=? AND s.user_id=?", (artifact_id, user_id)).fetchone()
            if row is None or (session_id is not None and row["session_id"] != session_id):
                raise StoreError(404, "文件不存在")
            item = dict(row)
        return self._public_artifact(item), self.resolve_run_file(item["run_id"], item["relative_path"])

    def artifacts(self, session_id: str, user_id: str, *, run_id: str | None = None, completed_only: bool = False, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            self._session(db, session_id, user_id)
            rows = db.execute("""SELECT a.* FROM artifacts a JOIN runs r ON a.run_id=r.run_id
                WHERE a.session_id=? AND (? IS NULL OR a.run_id=?) AND (?=0 OR r.status='completed')
                ORDER BY a.created_at DESC,a.artifact_id LIMIT ?""", (session_id, run_id, run_id, int(completed_only), limit)).fetchall()
            return [self._public_artifact(dict(row)) for row in rows]
