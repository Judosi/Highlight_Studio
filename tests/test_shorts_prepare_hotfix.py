from pathlib import Path
from types import SimpleNamespace
import subprocess
import shutil
import pytest

from highlight_studio.services import pipeline as p
from highlight_studio.core import utils


def test_prepare_does_not_invent_eta(tmp_path):
    logger = p.JobLogger(tmp_path)
    logger.heartbeat('shorts_prepare', 2, 'Подготовка', eta_seconds=None)
    status = p.read_json(tmp_path/'status.json', {})
    assert status['eta_seconds'] is None
    assert 'estimated_finish_at' not in status


@pytest.mark.parametrize('failure', ['timeout', 'crash', 'empty'])
def test_caption_worker_failure_falls_back_once_and_cleans_requests(tmp_path, monkeypatch, failure):
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        assert kw['timeout'] == 300
        assert kw['cancel_file'] == p.cancel_path(tmp_path)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(cmd, 300)
        return SimpleNamespace(returncode=-7 if failure == 'crash' else 0, stdout='')
    monkeypatch.setattr(p, 'run_cmd', run)
    settings = {'shorts_caption_quality':'high'}
    logger = p.JobLogger(tmp_path)
    for index in [1, 2]:
        assert p._shorts_refined_transcript_bounded(tmp_path, tmp_path/'input.mp4', 0, 4, settings, logger, index) is None
    assert len(calls) == 1
    assert p.read_json(tmp_path/'shorts_caption_warning.json', {})['fallback'] is True
    assert not list((tmp_path/'shorts_cache/workers').glob('*.json'))


def test_caption_worker_cancel_does_not_fall_back(tmp_path, monkeypatch):
    def run(*args, **kw): raise p.OperationCancelled('cancelled')
    monkeypatch.setattr(p, 'run_cmd', run)
    settings = {}
    with pytest.raises(p.OperationCancelled):
        p._shorts_refined_transcript_bounded(tmp_path, tmp_path/'input.mp4', 0, 4, settings, p.JobLogger(tmp_path), 1)
    assert not settings.get('_shorts_asr_unavailable')
    assert not (tmp_path/'shorts_caption_warning.json').exists()


def test_caption_worker_success_preserves_rows(tmp_path, monkeypatch):
    rows = [{'start':1., 'end':2., 'text':'Проверенная реплика', 'words':[]}]
    def run(cmd, **kw):
        p.write_json(Path(cmd[-1]), {'rows':rows})
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(p, 'run_cmd', run)
    assert p._shorts_refined_transcript_bounded(tmp_path, tmp_path/'input.mp4', 0, 4, {}, p.JobLogger(tmp_path), 1) == rows


def test_short_preparation_media_probes_have_time_limits(monkeypatch, tmp_path):
    calls = []
    def run(cmd, **kw):
        calls.append(kw)
        return SimpleNamespace(returncode=0, stdout='12' if 'format=duration' in cmd else '{"streams":[]}')
    monkeypatch.setattr(utils, 'run_cmd', run)
    assert utils.video_duration(tmp_path/'video.mp4') == 12
    assert utils.audio_streams(tmp_path/'video.mp4') == []
    assert all(c['timeout'] == 60 for c in calls)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg unavailable')
def test_real_vertical_render_with_caption_worker_crash(tmp_path, monkeypatch):
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=15',
                    '-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t','4',
                    '-c:v','libx264','-preset','ultrafast','-c:a','aac',str(tmp_path/'input.mp4')], check=True, timeout=30)
    p.write_json(tmp_path/'content_factory/shorts_candidates.json', [{'start':.2,'end':3.5,'score':9,'title':'Проверка субтитров'}])
    p.write_json(tmp_path/'transcript.json', [{'start':.4,'end':2.8,'text':'Проверка субтитров после сбоя модели'}])
    original = p.run_cmd
    def run(cmd, **kw):
        if '_shorts_caption_worker' in ' '.join(cmd):
            return SimpleNamespace(returncode=-7, stdout='native crash')
        return original(cmd, **kw)
    monkeypatch.setattr(p, 'run_cmd', run)
    result = p.render_shorts_candidates(tmp_path, {'shorts_count':1,'shorts_min_seconds':2,
        'shorts_max_seconds':45,'shorts_reframe_mode':'smart_zoom','shorts_caption_quality':'high',
        'shorts_burn_subtitles':True,'shorts_dynamic_captions':True,'shorts_hook_title_enabled':True,
        'shorts_normalize_audio':False,'video_encoder':'libx264','shorts_render_preset':'ultrafast'}, p.JobLogger(tmp_path))
    assert len(result['rendered']) == 1
    assert result['rendered'][0]['width'] == 1080
    assert result['rendered'][0]['height'] == 1920
    assert result['caption_warning']['fallback'] is True
    assert 'Проверка' in (tmp_path/'outputs/shorts/short_01.ass').read_text()
    assert p.read_json(tmp_path/'status.json', {})['state'] == 'done'
