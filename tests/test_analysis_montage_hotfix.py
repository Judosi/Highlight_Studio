from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

import highlight_studio.api.app as api
import highlight_studio.services.pipeline as pipeline
from highlight_studio.services.whisper_worker import WhisperProcess
from highlight_studio.core.revisions import mark_analysis_complete, freshness_report
from highlight_studio.core.utils import OperationCancelled, read_json, write_json


@pytest.fixture
def project(tmp_path, monkeypatch):
    settings = api.default_settings()
    write_json(tmp_path / 'project.json', {'settings': settings})
    items = [{'id': i, 'start': i * 20, 'end': i * 20 + 10, 'title': f'Clip {i}',
              'score': 8, 'candidate_id': f'original-{i}', 'source_candidate_key': f'key-{i}'} for i in range(1, 4)]
    write_json(tmp_path / 'segments.json', items)
    write_json(tmp_path / 'candidates.json', items)
    mark_analysis_complete(tmp_path, settings)
    monkeypatch.setattr(api, 'project_dir', lambda _: tmp_path)
    monkeypatch.setattr(api, 'video_duration', lambda _: pytest.fail('Deletion must not probe source media'))
    monkeypatch.setattr(pipeline, 'video_duration', lambda _: pytest.fail('Deletion must not probe source media'))
    return tmp_path, settings, items


def test_remove_persists_one_clip_preserves_metadata_and_supports_undo(project):
    path, settings, items = project
    revision = api.segments_revision(path, settings)
    result = api.remove_project_segment('p', {'item': items[1], 'expected_revision': revision})
    assert [x['candidate_id'] for x in result['segments']] == ['original-1', 'original-3']
    assert [x['id'] for x in result['segments']] == [1, 2]
    assert read_json(path / 'segments.json', []) == result['segments']
    assert freshness_report(path, settings)['segments_current']
    request = Request({'type': 'http', 'headers': [(b'x-segments-revision', result['segments_revision'].encode())]})
    api.put_segments('p', items, request, Response())
    assert len(read_json(path / 'segments.json', [])) == 3
    assert freshness_report(path, settings)['segments_current']


def test_stale_remove_cannot_delete_another_clip(project):
    path, settings, items = project
    old = api.segments_revision(path, settings)
    api.remove_project_segment('p', {'item': items[0], 'expected_revision': old})
    with pytest.raises(HTTPException) as exc:
        api.remove_project_segment('p', {'item': items[1], 'expected_revision': old})
    assert exc.value.status_code == 409
    assert len(read_json(path / 'segments.json', [])) == 2


def test_remove_last_clip_and_reject_malformed_target(project):
    path, settings, _ = project
    while items := read_json(path / 'segments.json', []):
        revision = api.segments_revision(path, settings)
        api.remove_project_segment('p', {'item': items[0], 'expected_revision': revision})
    assert read_json(path / 'segments.json', None) == []
    with pytest.raises(HTTPException) as exc:
        api.remove_project_segment('p', {'item': {}, 'expected_revision': api.segments_revision(path, settings)})
    assert exc.value.status_code == 422


def test_legacy_hook_setting_does_not_move_best_clip(tmp_path):
    rows = [pipeline.Candidate(id=i, start=i*30, end=i*30+10, score=10 if i==3 else 7,
                               title=str(i), reason='', hook_potential='high', standalone_clarity=1) for i in (3, 1, 2)]
    ordered = pipeline.hook_first_story_order(tmp_path, rows, {'hook_first_enabled': True}, SimpleNamespace(log=lambda _: None))
    assert [x.id for x in ordered] == [1, 2, 3]
    assert len(ordered) == len(rows)
    assert all(x.story_role != 'hook' for x in ordered)


@pytest.fixture
def fake_native(tmp_path, monkeypatch):
    # A real spawned process imports this deterministic model in place of CUDA.
    (tmp_path / 'faster_whisper.py').write_text('''
import time
from types import SimpleNamespace
class WhisperModel:
    def __init__(self, model, **kwargs):
        if model == 'load_hang': time.sleep(60)
        if model == 'load_error': raise RuntimeError('GPU initialization failed')
    def transcribe(self, audio, **kwargs):
        def segments():
            if audio == 'hang': time.sleep(60)
            if audio == 'crash':
                import os
                os._exit(37)
            yield SimpleNamespace(start=1, end=2, text='test', words=[SimpleNamespace(start=1, end=2, word='test')])
        return segments(), None
''')
    monkeypatch.syspath_prepend(str(tmp_path))


def test_native_worker_keeps_model_for_multiple_chunks(fake_native):
    worker = WhisperProcess('base', cancel_check=lambda: None, heartbeat=lambda: None)
    pid = worker.process.pid
    try:
        for _ in range(2):
            segments, _ = worker.transcribe('audio')
            assert next(segments).words[0].word == 'test'
            assert list(segments) == []
            assert worker.process.pid == pid
    finally:
        worker.close()
    assert worker.process is None


@pytest.mark.parametrize('audio', ['hang', 'crash'])
def test_hung_or_crashed_native_worker_is_stopped(fake_native, audio):
    worker = WhisperProcess('base', cancel_check=lambda: None, heartbeat=lambda: None, stall_timeout=.3)
    with pytest.raises((TimeoutError, RuntimeError)):
        list(worker.transcribe(audio)[0])
    assert worker.process is None


def test_cancel_interrupts_native_inference(fake_native):
    cancel = False
    def check():
        if cancel:
            raise OperationCancelled('cancelled')
    worker = WhisperProcess('base', cancel_check=check, heartbeat=lambda: None)
    cancel = True
    with pytest.raises(OperationCancelled):
        list(worker.transcribe('hang')[0])
    assert worker.process is None


def test_model_load_is_bounded(fake_native):
    with pytest.raises(TimeoutError):
        WhisperProcess('load_hang', cancel_check=lambda: None, heartbeat=lambda: None, load_timeout=.3)


def test_waiting_worker_emits_heartbeat(fake_native):
    ticks = []
    worker = WhisperProcess('base', cancel_check=lambda: None, heartbeat=lambda: ticks.append(time.monotonic()), stall_timeout=5.4)
    with pytest.raises(TimeoutError):
        list(worker.transcribe('hang')[0])
    assert ticks


def test_pipeline_gpu_timeout_retries_cpu_keeps_completed_chunks(tmp_path, monkeypatch):
    import highlight_studio.services.whisper_worker as worker_module
    calls, closed = [], []
    class Model:
        def __init__(self, _name, **kwargs): self.device = kwargs['device']
        def close(self): closed.append(self.device)
        def transcribe(self, audio, **kwargs):
            calls.append((self.device, Path(audio).name))
            if self.device == 'cuda':
                raise TimeoutError('native GPU stall')
            return iter([SimpleNamespace(start=1., end=8., text='Проверка речи', words=[])]), None
    monkeypatch.setattr(worker_module, 'WhisperProcess', Model)
    monkeypatch.setattr(pipeline, 'transcript_fingerprint', lambda *a: 'generation')
    monkeypatch.setattr(pipeline, 'transcript_fingerprint_legacy', lambda *a: 'legacy')
    monkeypatch.setattr(pipeline, 'detect_hardware_capabilities', lambda: {
        'cpu': {'logical_threads': 2}, 'ctranslate2': {'cuda_ok': True, 'compute_types': ['int8_float32']}})
    monkeypatch.setattr(pipeline, 'extract_audio', lambda *a: None)
    monkeypatch.setattr(pipeline, 'video_duration', lambda *a: 120.)
    monkeypatch.setattr(pipeline, 'run_cmd', lambda *a, **kw: SimpleNamespace(returncode=0))
    first = tmp_path / 'transcript_chunks/generation/transcript_0001.json'
    write_json(first, [{'start': 1., 'end': 8., 'text': 'Уже готовая речь', 'words': []}])
    original = first.read_bytes()
    logger = pipeline.JobLogger(tmp_path)
    rows = pipeline.transcribe(tmp_path, {'whisper_device': 'cuda', 'whisper_compute': 'int8_float32', 'chunk_seconds': 60}, logger)
    assert calls == [('cuda', 'chunk_0002.wav'), ('cpu', 'chunk_0002.wav')]
    assert first.read_bytes() == original
    assert [row.start for row in rows] == [1., 61.]
    assert read_json(tmp_path/'status.json', {})['stage'] == 'transcription'
    assert read_json(tmp_path/'whisper_runtime.json', {})['effective_device'] == 'cpu'
    assert {'cuda', 'cpu'} <= set(closed)
