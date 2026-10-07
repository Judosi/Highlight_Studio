from __future__ import annotations

import sqlite3
from contextlib import closing
import time
import uuid
from typing import Any
from sqlalchemy import select

from ..core.settings import JOBS_DB, WEB_ACCOUNTS_ENABLED

if WEB_ACCOUNTS_ENABLED:
    from .database.engine import session_scope
    from .database.models import JobRecord


def _connect() -> sqlite3.Connection:
    JOBS_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(JOBS_DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,state TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,started_at REAL,updated_at REAL NOT NULL,finished_at REAL,error TEXT,pid INTEGER)"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at DESC)")
    con.commit()
    return con


def create_job(project_id: str, kind: str, title: str, state: str = "queued") -> str:
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            db.add(
                JobRecord(
                    id=job_id,
                    project_id=project_id,
                    kind=kind,
                    title=title,
                    state=state,
                    progress=0.0,
                    message=title,
                    created_at=now,
                    updated_at=now,
                )
            )
    else:
        with closing(_connect()) as con:
            con.execute(
                "INSERT INTO jobs(id,project_id,kind,title,state,progress,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, project_id, kind, title, state, 0.0, title, now, now),
            )
            con.commit()
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not job_id or not fields:
        return
    fields["updated_at"] = time.time()
    allowed = {"state", "progress", "message", "started_at", "finished_at", "error", "pid", "updated_at"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            row = db.get(JobRecord, job_id)
            if row:
                for key in keys:
                    setattr(row, key, fields[key])
    else:
        with closing(_connect()) as con:
            sql = "UPDATE jobs SET " + ", ".join(f"{k}=?" for k in keys) + " WHERE id=?"  # nosec B608
            con.execute(sql, [fields[k] for k in keys] + [job_id])
            con.commit()


def _job_dict(row) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {
        k: getattr(row, k)
        for k in (
            "id",
            "project_id",
            "kind",
            "title",
            "state",
            "progress",
            "message",
            "created_at",
            "started_at",
            "updated_at",
            "finished_at",
            "error",
            "pid",
        )
    }


def get_latest_job(project_id: str) -> dict[str, Any]:
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            return _job_dict(
                db.scalar(select(JobRecord).where(JobRecord.project_id == project_id).order_by(JobRecord.created_at.desc()).limit(1))
            )
    with closing(_connect()) as con:
        row = con.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
    return _job_dict(row)


def list_jobs(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            q = select(JobRecord)
            q = q.where(JobRecord.project_id == project_id) if project_id else q
            rows = db.scalars(q.order_by(JobRecord.created_at.desc()).limit(limit)).all()
            return [_job_dict(x) for x in rows]
    with closing(_connect()) as con:
        rows = (
            con.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            if project_id
            else con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        )
    return [_job_dict(x) for x in rows]


def recover_interrupted_jobs() -> None:
    now = time.time()
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            rows = db.scalars(select(JobRecord).where(JobRecord.state.in_(["queued", "running", "cancel_requested"]))).all()
            for row in rows:
                row.state = "interrupted"
                row.finished_at = now
                row.updated_at = now
                row.error = "Backend restarted while job was running"
    else:
        with closing(_connect()) as con:
            con.execute(
                "UPDATE jobs SET state='interrupted', finished_at=?, updated_at=?, error='Backend restarted while job was running' WHERE state IN ('queued','running','cancel_requested')",
                (now, now),
            )
            con.commit()
