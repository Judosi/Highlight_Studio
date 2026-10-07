from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from highlight_studio.api.app import creator_pack
from highlight_studio.core.utils import OperationCancelled, read_json, write_json
from highlight_studio.infrastructure.support_bundle import redact_text, _sanitize_value
from highlight_studio.integrations.ai.ollama import OllamaClient
from highlight_studio.services import pipeline as p


def montage(root, count=50):
    scenes = [dict(id=i+1, start=i*120., end=i*120.+45, score=10-i*.05,
                   title=f"Мастер проверяет двигатель номер {i+1}",
                   text_preview="Мастер проверяет двигатель. Ротор перестал вращаться.")
              for i in range(count)]
    write_json(root / 'segments.json', scenes)
    write_json(root / 'transcript.json', [])
    return scenes


def test_metadata_identity_covers_unsampled_scene_and_order(tmp_path):
    scenes = montage(tmp_path)
    before = p.build_stream_context(tmp_path, {}, max_items=5)
    sampled = {s['source_start'] for s in before['scenes']}
    index = next(i for i, s in enumerate(scenes) if s['start'] not in sampled)
    scenes[index]['end'] += 4
    write_json(tmp_path / 'segments.json', scenes)
    changed = p.build_stream_context(tmp_path, {}, max_items=5)
    assert changed['signature'] != before['signature']
    scenes.reverse()
    write_json(tmp_path / 'segments.json', scenes)
    assert p.build_stream_context(tmp_path, {}, max_items=5)['signature'] != changed['signature']


def test_metadata_identity_does_not_depend_on_prompt_sample_budget(tmp_path):
    montage(tmp_path)
    assert p.build_stream_context(tmp_path, {}, max_items=5)['signature'] == p.build_stream_context(tmp_path, {}, max_items=25)['signature']


def test_creator_pack_does_not_resurrect_removed_montage(tmp_path):
    scenes = montage(tmp_path, 2)
    write_json(tmp_path / 'candidates.json', scenes)
    write_json(tmp_path / 'segments.json', [])
    pack = creator_pack(tmp_path, {})
    assert pack['ok'] is False
    assert pack['titles'] == []
    assert pack['best_shorts'] == []


def test_metadata_uses_trimmed_transcript_over_old_candidate_preview(tmp_path):
    write_json(tmp_path/'segments.json', [dict(start=40, end=50, title='Разговор', score=8,
                                             text_preview='Дмитрий рассказывает про концерт')])
    write_json(tmp_path/'transcript.json', [dict(start=0, end=10, text='Дмитрий рассказывает про концерт'),
                                          dict(start=41, end=49, text='Мастер проверяет новый двигатель')])
    context = p.build_stream_context(tmp_path, {})
    assert context['scenes'][0]['transcript'] == 'Мастер проверяет новый двигатель'


def test_every_metadata_title_requires_its_own_evidence(tmp_path):
    montage(tmp_path, 2)
    context = p.build_stream_context(tmp_path, {})
    data = p.build_specific_metadata_fallback(tmp_path, {}, context=context)
    data['titles'] = ['Мастер проверяет двигатель', 'Ротор двигателя перестал вращаться', 'Бустер выиграл миллион в Париже']
    assert p.metadata_specificity_audit(data, context)['passed'] is False


def test_creator_pack_returns_short_summaries_and_hooks(tmp_path):
    montage(tmp_path, 2)
    pack = creator_pack(tmp_path, {})
    for name in ('short_titles', 'short_summary', 'montage_summary', 'hashtags', 'hook_options'):
        assert pack.get(name), name


def test_cancelled_metadata_preserves_previous_result(tmp_path, monkeypatch):
    montage(tmp_path, 2)
    old = {'titles': ['Мой сохранённый заголовок']}
    write_json(tmp_path/'youtube_metadata.json', old)
    def cancelled(*a, **kw):
        raise OperationCancelled('cancel requested')
    monkeypatch.setattr(p, 'make_ai_client', lambda *a, **kw: SimpleNamespace(generate_json=cancelled))
    monkeypatch.setattr(p.time, 'sleep', lambda *a: None)
    with pytest.raises(OperationCancelled):
        p.generate_youtube_metadata(tmp_path, {'metadata_ai_enabled': True, 'metadata_ai_retries': 1})
    assert read_json(tmp_path/'youtube_metadata.json') == old


@pytest.mark.parametrize('text,secret', [
    ('Authorization: Bearer secret-token-123', 'secret-token-123'),
    ('{"refresh_token": "secret-refresh-456"}', 'secret-refresh-456'),
    ('Cookie: session=secret-cookie-789; preference=dark', 'secret-cookie-789'),
])
def test_support_redacts_full_secret_values(text, secret):
    assert secret not in redact_text(text)


def test_support_redacts_nested_passwords_and_api_keys():
    data = _sanitize_value({'settings': {'smtp_password': 'smtp-secret', 'custom_api_key': 'key-secret'}})
    assert 'smtp-secret' not in json.dumps(data)
    assert 'key-secret' not in json.dumps(data)


def test_ollama_requires_stream_completion_marker(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_lines(self, **kw):
            yield json.dumps({'response': '{"blocks":[{"id":1,"score":9}]}', 'done': False})
    monkeypatch.setattr('highlight_studio.integrations.ai.ollama.requests.post', lambda *a, **kw: Response())
    with pytest.raises(p.AITransportError, match='(?i)(incomplete|completion|заверш)'):
        OllamaClient()._post_generate({'model': 'test'}, timeout=30)


def test_primary_context_requires_confident_live_evidence(tmp_path):
    block = p.Candidate(1, 0, 120, 9, 'Мастер чинит двигатель', '', decision='keep',
                        content_class='primary_live', content_class_confidence=.1,
                        standalone_clarity=.9)
    _, added = p.refill_with_primary_live_context(tmp_path, [block], [],
        [p.TranscriptSegment(0, 120, 'Мастер чинит двигатель')], 120, 90, {}, SimpleNamespace(log=lambda *a: None))
    assert added == []


def test_primary_context_does_not_revive_unconfirmed_model_reject(tmp_path):
    block = p.Candidate(1, 0, 120, 9, 'Проверка двигателя', 'повтор без новой информации', decision='remove',
                        content_class='primary_live', content_class_confidence=.95,
                        standalone_clarity=.9)
    _, added = p.refill_with_primary_live_context(tmp_path, [block], [],
        [p.TranscriptSegment(0, 120, 'Мастер чинит двигатель')], 120, 90, {}, SimpleNamespace(log=lambda *a: None))
    assert added == []


def test_audio_profile_can_be_reused_after_temporary_wav_cleanup(tmp_path, monkeypatch):
    montage(tmp_path, 1)
    signature = {'size': 12, 'partial_sha256': 'media', 'signature_version': 2}
    monkeypatch.setattr(p, 'video_content_signature', lambda *a: signature)
    profile = {'enabled': True, 'video_content_signature': signature, 'db': [-30., -20.],
               'window_sec': 1., 'p90': -20., 'silent_threshold': -45.}
    write_json(tmp_path/'audio_dynamics.json', profile)
    result = p.analyze_audio_dynamics(tmp_path, {}, SimpleNamespace(log=lambda *a: None))
    assert result['enabled'] is True
    assert result['db'] == profile['db']


@pytest.mark.parametrize('verdict', [
    {'score': 0}, {'score': 9, 'keep': False}, {'score': 9, 'keep': 'false'},
    {'score': 9, 'keep': True, 'decision': 'remove'},
])
def test_micro_reject_cannot_inherit_high_parent_score(tmp_path, monkeypatch, verdict):
    parent = p.Candidate(1, 0, 60, 9, 'Мастер чинит двигатель', '',
                         text_preview='Мастер проверяет двигатель и снимает ротор',
                         decision='keep', content_class='primary_live', content_class_confidence=.9)
    class AI:
        def warmup(self, **kw): return {'ok': True}
        def generate_json(self, *a, **kw):
            return {'clips': [dict(id=idx, title='Проверка двигателя', **verdict)
                              for idx in kw.get('expected_ids', {1})]}
    monkeypatch.setattr(p, 'make_ai_client', lambda *a, **kw: AI())
    monkeypatch.setattr(p, 'build_micro_windows_for_candidate', lambda *a: [dict(start=0, end=30, text=parent.text_preview, parent_id=1)])
    result = p.build_micro_candidates(tmp_path, [parent], [p.TranscriptSegment(0, 30, parent.text_preview)],
                                      {'full_ai_coverage': True, 'micro_cut_enabled': True}, p.JobLogger(tmp_path))
    assert not [x for x in result if p.candidate_is_selectable(x, {})]
    assert read_json(tmp_path/'micro_rejections.json', [])


def test_refill_respects_micro_rejection_and_long_speech_gap(tmp_path):
    parent = p.Candidate(1, 0, 180, 9, 'Мастер чинит двигатель', '', decision='keep',
                        content_class='primary_live', content_class_confidence=.95)
    transcript = [p.TranscriptSegment(0, 20, 'Мастер открывает двигатель'),
                  p.TranscriptSegment(120, 180, 'Мастер проверяет ротор')]
    _, added = p.refill_with_primary_live_context(tmp_path, [parent], [], transcript, 180, 180, {},
        SimpleNamespace(log=lambda *a: None), rejected_ranges=[dict(start=140, end=160)])
    assert all(not (x.start < 119 and x.end > 21) for x in added)
    assert all(not (x.start < 160 and x.end > 140) for x in added)


def fake_whisper(monkeypatch, tmp_path, returned=None):
    calls = []
    class Model:
        def __init__(self, *a, **kw): pass
        def transcribe(self, *a, **kw):
            calls.append('transcribe')
            rows = returned if returned is not None else [SimpleNamespace(start=1., end=8., text='Мастер проверяет двигатель', words=[])]
            return iter(rows), None
    import highlight_studio.services.whisper_worker as worker_module
    Model.close = lambda self: None
    monkeypatch.setattr(worker_module, 'WhisperProcess', Model)
    monkeypatch.setattr(p, 'transcript_fingerprint', lambda *a: 'verified-generation')
    monkeypatch.setattr(p, 'transcript_fingerprint_legacy', lambda *a: 'legacy')
    monkeypatch.setattr(p, 'prepare_nvidia_dll_paths', lambda: None)
    monkeypatch.setattr(p, 'detect_hardware_capabilities', lambda: {'cpu': {'logical_threads': 2}})
    monkeypatch.setattr(p, 'extract_audio', lambda *a: None)
    monkeypatch.setattr(p, 'video_duration', lambda *a: 10.)
    monkeypatch.setattr(p, 'run_cmd', lambda *a, **kw: SimpleNamespace(returncode=0))
    return calls


def test_corrupt_whisper_chunk_is_recomputed_not_silently_skipped(tmp_path, monkeypatch):
    calls = fake_whisper(monkeypatch, tmp_path)
    chunk = tmp_path/'transcript_chunks'/'verified-generation'/'transcript_0001.json'
    chunk.parent.mkdir(parents=True)
    chunk.write_text('{"half_written":', encoding='utf-8')
    result = p.transcribe(tmp_path, {'whisper_device': 'cpu'}, p.JobLogger(tmp_path))
    assert calls == ['transcribe']
    assert result[0].text == 'Мастер проверяет двигатель'


@pytest.mark.parametrize('bad', [
    [dict(start='NaN', end=10, text='bad')],
    [dict(start=0, end=-1, text='bad')],
    [dict(start=0, end=10, text='bad', words=['not a word object'])],
    {'unexpected': []},
])
def test_invalid_whisper_cache_is_recomputed(tmp_path, monkeypatch, bad):
    calls = fake_whisper(monkeypatch, tmp_path)
    write_json(tmp_path/'transcript_manifest.json', {'fingerprint': 'verified-generation'})
    write_json(tmp_path/'transcript.json', bad)
    result = p.transcribe(tmp_path, {'whisper_device': 'cpu'}, p.JobLogger(tmp_path))
    assert calls == ['transcribe']
    assert all(x.start >= 0 and x.end > x.start for x in result)


def test_valid_empty_whisper_cache_does_not_rerun(tmp_path, monkeypatch):
    calls = fake_whisper(monkeypatch, tmp_path)
    write_json(tmp_path/'transcript_manifest.json', {'fingerprint': 'verified-generation'})
    write_json(tmp_path/'transcript.json', [])
    assert p.transcribe(tmp_path, {'whisper_device': 'cpu'}, p.JobLogger(tmp_path)) == []
    assert calls == []


def test_calm_irl_story_is_not_outscored_only_by_loudness_and_shortness():
    story = p.Candidate(1, 0, 65, 9.4, 'Гость объясняет причину переезда', 'законченная личная история',
                        confidence=8, standalone_clarity=.95, moment_type='story',
                        content_class='primary_live', content_class_confidence=.9)
    loud = p.Candidate(2, 100, 115, 6.8, 'Громкая реакция', 'крик', audio_score=2.,
                       confidence=6, standalone_clarity=.55, content_class='primary_live', content_class_confidence=.9)
    settings = {'content_type': 'IRL стрим', 'edit_mode': 'Сбалансированный'}
    assert p.candidate_selection_key(story, settings) > p.candidate_selection_key(loud, settings)


@pytest.mark.parametrize('seconds', [1800, 3600, 10800, 21600, 39600])
def test_long_vod_late_scenes_get_micro_and_metadata_coverage(tmp_path, seconds):
    blocks = [p.Candidate(i+1, float(t), float(t+120), 7., f'Проверка двигателя {i}', '',
                          content_class='primary_live', content_class_confidence=.9)
              for i, t in enumerate(range(0, seconds, 180))]
    selected, report = p.select_micro_source_blocks(blocks, min(3600., seconds/2), {})
    assert any(c.start >= seconds-900 for c in selected)
    write_json(tmp_path/'segments.json', [vars(c) for c in blocks])
    context = p.build_stream_context(tmp_path, {}, max_items=5)
    assert context['scenes'][-1]['source_start'] == blocks[-1].start
    chapters = p.build_specific_metadata_fallback(tmp_path, {}, context=context)['chapters']
    assert blocks[-1].title in chapters[-1]


def test_upload_final_intent_is_durable_before_network_send(tmp_path, monkeypatch):
    from highlight_studio.integrations.youtube import publisher
    video = tmp_path/'output.mp4'
    video.write_bytes(b'fake-video-payload')
    monkeypatch.setattr(publisher, '_init_resumable_upload', lambda *a: ('https://example.invalid/session', 'Test', 'private'))
    def crash_before_reply(*a, **kw):
        state = publisher._read_upload_state(tmp_path)
        record = next(iter(state['intents'].values()))
        assert record['status'] == 'finalizing'
        assert record['offset'] == video.stat().st_size
        raise OperationCancelled('simulated shutdown during final request')
    monkeypatch.setattr(publisher.requests, 'put', crash_before_reply)
    with pytest.raises(OperationCancelled):
        publisher._upload_one('test-token', video, {'title': 'Test'}, project_dir=tmp_path)


def test_media_validation_rejects_nonfinite_duration(tmp_path, monkeypatch):
    from highlight_studio.core import artifacts
    video = tmp_path/'test.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(artifacts.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout=json.dumps({'streams':[{'codec_type':'video'}], 'format':{'duration':'NaN'}})))
    assert artifacts.validate_media_file(video)['ok'] is False


def test_interrupted_render_part_preserves_previous_valid_cache(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from backend.main import default_settings
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg unavailable')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=20', '-f', 'lavfi', '-i',
                    'sine=frequency=880:sample_rate=48000', '-t', '2', '-c:v', 'libx264', '-c:a', 'aac',
                    str(tmp_path/'input.mp4')], check=True, timeout=30)
    write_json(tmp_path/'segments.json', [{'id':1, 'start':0., 'end':1.5, 'score':8}])
    settings = {**default_settings(), 'video_encoder':'libx264', 'render_preset':'ultrafast',
                'make_srt':False, 'remove_silence':False}
    p.render(tmp_path, settings, p.JobLogger(tmp_path))
    part = tmp_path/'render_parts/part_001.mp4'
    manifest = tmp_path/'render_parts/part_001.json'
    before, before_meta = part.read_bytes(), manifest.read_bytes()
    original_run = p.run_cmd
    def interrupt(cmd, **kw):
        if str(cmd[-1]).endswith('part_001.pending.mp4'):
            Path(cmd[-1]).write_bytes(b'incomplete' * 1024)
            raise OperationCancelled('simulated interruption')
        return original_run(cmd, **kw)
    monkeypatch.setattr(p, 'run_cmd', interrupt)
    with pytest.raises(OperationCancelled):
        p.render(tmp_path, {**settings, 'crf':17}, p.JobLogger(tmp_path))
    assert part.read_bytes() == before
    assert manifest.read_bytes() == before_meta
    monkeypatch.setattr(p, 'run_cmd', original_run)
    part.write_bytes(b'corrupt-cache' * 1024)
    p.render(tmp_path, settings, p.JobLogger(tmp_path))
    assert part.read_bytes() != b'corrupt-cache' * 1024
    assert p.validate_media_file(part)['ok'] is True


@pytest.mark.parametrize('code,output', [(-7, 'native crash'), (127, 'timeout'), (0, 'not json')])
def test_native_hardware_probe_failure_does_not_crash_server(monkeypatch, code, output):
    from highlight_studio.services import hardware
    monkeypatch.setattr(hardware, '_run', lambda *a, **kw: (code, output))
    assert hardware._ctranslate2_probe()['cuda_ok'] is False


def test_native_hardware_probe_preserves_valid_child_result(monkeypatch):
    from highlight_studio.services import hardware
    result = {'ok':True, 'installed':True, 'cuda_ok':True, 'cuda_device_count':1,
              'version':'test', 'compute_types':['int8_float32']}
    monkeypatch.setattr(hardware, '_run', lambda *a, **kw: (0, 'HS_CT2_RESULT='+json.dumps(result)))
    assert hardware._ctranslate2_probe() == result
