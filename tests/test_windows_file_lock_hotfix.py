from __future__ import annotations

import errno
import json
import threading
from pathlib import Path

from backend.src.highlight_studio.core import utils
from backend.src.highlight_studio.services import pipeline


def _permission_error(message: str = "locked") -> PermissionError:
    exc = PermissionError(errno.EACCES, message)
    exc.winerror = 5  # type: ignore[attr-defined]
    return exc


def test_write_json_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    real_replace = utils.os.replace
    calls = {"count": 0}

    def flaky_replace(src, dst):
        if Path(dst) != target:
            return real_replace(src, dst)
        calls["count"] += 1
        if calls["count"] <= 3:
            raise _permission_error()
        return real_replace(src, dst)

    monkeypatch.setattr(utils.os, "replace", flaky_replace)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    utils.write_json(target, {"state": "running", "progress": 42})

    assert calls["count"] == 4
    assert json.loads(target.read_text(encoding="utf-8"))["progress"] == 42
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_concurrent_status_writes_remain_valid(tmp_path):
    target = tmp_path / "status.json"
    errors: list[BaseException] = []

    def writer(worker_id: int):
        try:
            for item in range(30):
                utils.write_json(target, {"worker": worker_id, "item": item})
        except BaseException as exc:  # pragma: no cover - assertion captures thread errors
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(idx,)) for idx in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["worker"] in range(6)
    assert payload["item"] in range(30)
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_job_logger_does_not_abort_on_persistent_status_lock(tmp_path, monkeypatch):
    logger = pipeline.JobLogger(tmp_path, reset_status=False)

    def locked_write(path: Path, _data):
        if path.name == "status.json":
            raise _permission_error("Windows Defender lock")
        return utils.write_json(path, _data)

    monkeypatch.setattr(pipeline, "write_json", locked_write)

    # Progress persistence is advisory; a transient lock must not fail the job.
    logger.set_status("running", 12, "Downloading", stage="twitch_download")

    log_text = (tmp_path / "logs.txt").read_text(encoding="utf-8")
    assert "задача продолжена" in log_text
    assert "Downloading" in log_text


def test_non_lock_write_error_still_propagates(tmp_path, monkeypatch):
    logger = pipeline.JobLogger(tmp_path, reset_status=False)

    def disk_full(_path: Path, _data):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(pipeline, "write_json", disk_full)

    try:
        logger.set_status("running", 12, "Downloading")
    except OSError as exc:
        assert exc.errno == errno.ENOSPC
    else:  # pragma: no cover
        raise AssertionError("non-lock I/O error must propagate")
