from types import SimpleNamespace

import pytest

from highlight_studio.core import utils
from highlight_studio.services import pipeline


def metadata_project(path):
    utils.write_json(path / 'segments.json', [dict(id=1, start=0, end=30, score=9,
        title='Мастер проверяет двигатель', text_preview='Ротор двигателя перестал вращаться')])
    previous = pipeline.generate_youtube_metadata(path, {'metadata_ai_enabled': False})
    previous['ai_used'] = True
    utils.write_json(path / 'youtube_metadata.json', previous)
    return previous


@pytest.mark.parametrize('strict', [False, True])
def test_ai_failure_preserves_saved_metadata(tmp_path, monkeypatch, strict):
    previous = metadata_project(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(pipeline, 'make_ai_client', lambda *a, **kw: SimpleNamespace(generate_json=fail))
    settings = {'metadata_ai_enabled': True, 'metadata_ai_retries': 1, 'require_ai_metadata': strict}
    with pytest.raises(RuntimeError):
        pipeline.generate_youtube_metadata(tmp_path, settings)
    assert utils.read_json(tmp_path / 'youtube_metadata.json') == previous


def test_empty_montage_does_not_erase_metadata(tmp_path):
    previous = metadata_project(tmp_path)
    utils.write_json(tmp_path / 'segments.json', [])
    assert pipeline.generate_youtube_metadata(tmp_path, {})['ok'] is False
    assert utils.read_json(tmp_path / 'youtube_metadata.json') == previous


def test_cancel_before_empty_montage_preserves_metadata(tmp_path):
    previous = metadata_project(tmp_path)
    utils.write_json(tmp_path / 'segments.json', [])
    pipeline.cancel_path(tmp_path).write_text('cancel')
    with pytest.raises(utils.OperationCancelled):
        pipeline.generate_youtube_metadata(tmp_path, {})
    assert utils.read_json(tmp_path / 'youtube_metadata.json') == previous


def test_failed_write_preserves_last_valid_backup(tmp_path, monkeypatch):
    target = tmp_path / 'segments.json'
    utils.write_json(target, [{'id': 1}])
    utils.write_json(target, [{'id': 2}])
    target.write_text('{broken')
    def disk_full(*args, **kwargs):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(utils, '_replace_with_retry', disk_full)
    with pytest.raises(OSError):
        utils.write_json(target, [{'id': 3}])
    assert utils.read_json(target) == [{'id': 1}]


@pytest.mark.parametrize('value', ['NaN', 'inf', '-1', '0'])
def test_probe_rejects_invalid_duration(tmp_path, monkeypatch, value):
    monkeypatch.setattr(utils, 'run_cmd', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=value))
    with pytest.raises(ValueError):
        utils.video_duration(tmp_path / 'video.mp4')


def test_probe_does_not_invent_video_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, 'run_cmd', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{"streams": []}'))
    assert utils.video_info(tmp_path / 'audio.mp4').get('error')


def test_job_logger_redacts_credentials_before_disk(tmp_path):
    logger = pipeline.JobLogger(tmp_path)
    logger.log('Authorization: Bearer secret-1127')
    assert 'secret-1127' not in (tmp_path / 'logs.txt').read_text()


def test_support_redacts_camel_case_keys():
    from highlight_studio.infrastructure.support_bundle import _sanitize_value
    assert _sanitize_value({'accessToken': 'secret', 'clientSecret': 'secret'}) == {
        'accessToken': '<REDACTED>', 'clientSecret': '<REDACTED>'}


@pytest.mark.parametrize('payload', ['{}', '{broken', '{"version":"11.2.7","app_version":"v11.2.6-old","design_id":"x"}'])
def test_identity_never_falls_back_to_an_old_release(tmp_path, payload):
    from highlight_studio.core.settings import load_release_identity
    (tmp_path / 'release_identity.json').write_text(payload)
    with pytest.raises(RuntimeError, match='release_identity'):
        load_release_identity(tmp_path)


@pytest.mark.parametrize('strict', [False, True])
def test_failed_metadata_job_cannot_report_done(tmp_path, monkeypatch, strict):
    from highlight_studio.api import app
    statuses = []
    logger = SimpleNamespace(set_status=lambda *args: statuses.append(args))
    monkeypatch.setattr(app, 'generate_youtube_metadata', lambda *args: {'ok': False, 'error': 'No scenes'})
    monkeypatch.setattr(app, 'start_background_job', lambda pid, message, task, **kw: task(tmp_path, {}, logger))
    with pytest.raises(RuntimeError, match='No scenes'):
        (app.post_metadata_ai_strict if strict else app.post_metadata_ai)('project')
    assert statuses == []


def test_local_fallback_job_has_an_honest_status(tmp_path, monkeypatch):
    from highlight_studio.api import app
    statuses = []
    logger = SimpleNamespace(set_status=lambda *args: statuses.append(args))
    monkeypatch.setattr(app, 'generate_youtube_metadata', lambda *args: {'ok': True, 'ai_used': False})
    monkeypatch.setattr(app, 'start_background_job', lambda pid, message, task, **kw: task(tmp_path, {}, logger))
    app.post_metadata_ai('project')
    assert statuses[0][0] == 'done'
    assert 'AI не использован' in statuses[0][2]


def test_metadata_failure_redacts_secret_in_error_and_log(tmp_path, monkeypatch):
    metadata_project(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('request failed: access_token=private-1127')
    monkeypatch.setattr(pipeline, 'make_ai_client', lambda *a, **kw: SimpleNamespace(generate_json=fail))
    with pytest.raises(RuntimeError) as error:
        pipeline.generate_youtube_metadata(tmp_path, {'metadata_ai_enabled': True, 'metadata_ai_retries': 1})
    assert 'private-1127' not in str(error.value)
    assert 'private-1127' not in (tmp_path / 'logs.txt').read_text()
