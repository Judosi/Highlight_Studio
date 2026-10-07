"""Cancellable, persistent Whisper process (native CUDA hangs cannot hold the API)."""
from __future__ import annotations

import multiprocessing
import os
import time
from types import SimpleNamespace


def _worker(connection, model_name, kwargs):
    try:
        os.environ.setdefault("OMP_NUM_THREADS", str(kwargs.get("cpu_threads", 4)))
        from .hardware import prepare_nvidia_dll_paths
        prepare_nvidia_dll_paths()
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, **kwargs)
        connection.send(("ready", None))
        while True:
            request = connection.recv()
            if request is None:
                break
            audio, options = request
            segments, _ = model.transcribe(audio, **options)
            for segment in segments:
                connection.send(("segment", {
                    "start": segment.start, "end": segment.end, "text": segment.text,
                    "words": [{"start": word.start, "end": word.end, "word": word.word}
                              for word in (segment.words or [])],
                }))
            connection.send(("done", None))
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as exc:
        try:
            connection.send(("error", f"{type(exc).__name__}: {exc}"))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class WhisperProcess:
    def __init__(self, model_name, *, cancel_check, heartbeat, load_timeout=600,
                 stall_timeout=300, **kwargs):
        self.cancel_check = cancel_check
        self.heartbeat = heartbeat
        self.stall_timeout = stall_timeout
        self.process = None
        self.connection = None
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, model_name, kwargs), daemon=True)
        try:
            self.process.start()
            child.close()
            kind, _ = self._receive(load_timeout)
            if kind != "ready":
                raise RuntimeError("Whisper worker did not initialize")
        except BaseException:
            child.close()
            self.close()
            raise

    def _receive(self, timeout):
        started = last_heartbeat = time.monotonic()
        while True:
            self.cancel_check()
            if self.connection.poll(0.2):
                try:
                    kind, payload = self.connection.recv()
                except (EOFError, OSError) as exc:
                    raise RuntimeError("Whisper process exited unexpectedly") from exc
                if kind == "error":
                    raise RuntimeError(payload)
                return kind, payload
            if not self.process.is_alive():
                raise RuntimeError(f"Whisper process exited (code {self.process.exitcode})")
            now = time.monotonic()
            if now - started >= timeout:
                raise TimeoutError(f"Whisper не отвечает более {timeout:g} с")
            if now - last_heartbeat >= 5:
                self.heartbeat()
                last_heartbeat = now

    def transcribe(self, audio, **options):
        def iterate():
            try:
                self.connection.send((audio, options))
                while True:
                    kind, payload = self._receive(self.stall_timeout)
                    if kind == "done":
                        return
                    if kind != "segment":
                        raise RuntimeError(f"Unexpected Whisper message: {kind}")
                    payload["words"] = [SimpleNamespace(**word) for word in payload["words"]]
                    yield SimpleNamespace(**payload)
            except BaseException:
                self.close()
                raise
        return iterate(), None

    def close(self):
        process, self.process = self.process, None
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
        if self.connection is not None:
            self.connection.close()
            self.connection = None
