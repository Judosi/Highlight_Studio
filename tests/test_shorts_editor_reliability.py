import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.src.highlight_studio.api import app as api
from backend.src.highlight_studio.services import pipeline
from backend.src.highlight_studio.core.utils import read_json, write_json


@pytest.fixture
def editor_project(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "PROJECTS_DIR", tmp_path)
    project = tmp_path / "editor"
    project.mkdir()
    write_json(project / "project.json", {"id": "editor", "settings": {"shorts_max_seconds": 60}})
    (project / "input.mp4").write_bytes(b"source")
    pipeline.remember_source_duration(project, 200)
    write_json(project / "content_factory/shorts_candidates.json", [
        {"title": "first", "start": 0, "end": 20, "caption_text": "old"},
        {"title": "second", "start": 50, "end": 70},
    ])
    write_json(project / "content_factory/content_factory_manifest.json", {"keep": "montage"})
    monkeypatch.setattr(api, "freshness_report", lambda *_: {"segments_current": True})
    client = TestClient(api.app)
    client.headers.update({"X-Local-Token": api.LOCAL_AUTH_TOKEN})
    return project, client


def edit_payload(**overrides):
    return {"title": "  Новый   момент  ", "start": 2.125, "end": 25.875,
            "hook_text": "Начало", "caption_text": None, "reframe_mode": "fit", **overrides}


def test_save_persists_editor_candidate_without_render(editor_project):
    project, client = editor_project
    response = client.put("/api/projects/editor/shorts/1", json=edit_payload())
    assert response.status_code == 200, response.text
    candidate = response.json()["candidate"]
    assert candidate["title"] == "Новый момент"
    assert candidate["start"] == 2.125 and candidate["end"] == 25.875
    assert candidate["render_dirty"] is True
    assert "caption_text" not in candidate
    saved = read_json(project / "content_factory/shorts_candidates.json", [])
    assert saved[0] == candidate and saved[1]["title"] == "second"
    manifest = read_json(project / "content_factory/content_factory_manifest.json", {})
    assert manifest["shorts"] == saved and manifest["keep"] == "montage"
    assert not (project / "status.json").exists()


def test_save_empty_caption_is_explicit_override(editor_project):
    project, client = editor_project
    response = client.put("/api/projects/editor/shorts/1", json=edit_payload(caption_text=""))
    assert response.status_code == 200
    assert read_json(project / "content_factory/shorts_candidates.json", [])[0]["caption_text"] == ""


@pytest.mark.parametrize("changes", [
    {"title": " \n "}, {"end": 100}, {"start": 190, "end": 210},
    {"start": 20, "end": 10}, {"end": "Infinity"}, {"start": "NaN"},
    {"start": 10, "end": 10.5},
])
@pytest.mark.parametrize("suffix,method", [("", "put"), ("/render", "post")])
def test_invalid_edit_never_mutates(editor_project, changes, suffix, method):
    project, client = editor_project
    path = project / "content_factory/shorts_candidates.json"
    before = path.read_bytes()
    response = getattr(client, method)(f"/api/projects/editor/shorts/1{suffix}", json=edit_payload(caption_text="", **changes))
    assert response.status_code == 422, response.text
    assert path.read_bytes() == before


def test_busy_save_and_render_do_not_mutate(editor_project, monkeypatch):
    project, client = editor_project
    monkeypatch.setitem(api.jobs, "editor", SimpleNamespace(is_alive=lambda: True))
    path = project / "content_factory/shorts_candidates.json"
    before = path.read_bytes()
    response = client.put("/api/projects/editor/shorts/1", json=edit_payload())
    assert response.status_code == 409
    response = client.post("/api/projects/editor/shorts/1/render", json=edit_payload())
    assert response.status_code == 200 and response.json()["started"] is False
    assert path.read_bytes() == before


def render_fixture(tmp_path, monkeypatch, *, fail=False):
    (tmp_path / "input.mp4").write_bytes(b"source")
    items = [
        {"title": "first weak", "start": 0, "end": 10, "score": 1, "render_dirty": True},
        {"title": "second strong", "start": 20, "end": 30, "score": 9, "render_dirty": True},
        {"title": "third edited overlap", "start": 20.125, "end": 21.875, "score": 0,
         "caption_text": "", "render_dirty": True, "bounds_edited": True},
    ]
    write_json(tmp_path / "content_factory/shorts_candidates.json", items)
    output = tmp_path / "outputs/shorts/short_01.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"previous-output" * 1024)
    write_json(tmp_path / "shorts_render_manifest.json", {"rendered": [
        {"index": 1, "path": "outputs/shorts/short_01.mp4", "title": "unchanged"},
    ]})
    monkeypatch.setattr(pipeline, "video_duration", lambda *_: 200)
    monkeypatch.setattr(pipeline, "audio_streams", lambda *_: [])
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *_: (["-c:v", "libx264"], "libx264"))
    monkeypatch.setattr(pipeline, "_validate_short_output", lambda *_, **kw: (
        True, [], {"duration_seconds": kw["expected_duration"], "width": 1280, "height": 720}))
    def run(cmd, **_):
        if not fail:
            Path(cmd[-1]).write_bytes(b"new-output" * 2048)
        return SimpleNamespace(returncode=1 if fail else 0, stdout="failed" if fail else "")
    monkeypatch.setattr(pipeline, "run_cmd", run)
    return items


def test_single_render_uses_original_index_exact_bounds_and_no_reselection(tmp_path, monkeypatch):
    render_fixture(tmp_path, monkeypatch)
    def unexpected(*_, **__):
        pytest.fail("single render must not reselect candidates or trim edited bounds")
    monkeypatch.setattr(pipeline, "_ensure_shorts_candidates_current", unexpected)
    monkeypatch.setattr(pipeline, "_tighten_short_bounds_to_speech", unexpected)
    old_bytes = (tmp_path / "outputs/shorts/short_01.mp4").read_bytes()
    result = pipeline.render_shorts_candidates(tmp_path, {
        "shorts_count": 1, "shorts_min_seconds": 3, "shorts_max_seconds": 60,
        "shorts_burn_subtitles": False, "shorts_vertical_reframe": False, "shorts_trim_silence": True,
    }, pipeline.JobLogger(tmp_path), only_indexes={3})
    rows = {row["index"]: row for row in result["rendered"]}
    assert rows[3]["title"] == "third edited overlap"
    assert (rows[3]["start"], rows[3]["end"]) == (20.125, 21.875)
    assert rows[3]["path"] == "outputs/shorts/short_03.mp4"
    assert (tmp_path / "outputs/shorts/short_01.mp4").read_bytes() == old_bytes
    candidates = read_json(tmp_path / "content_factory/shorts_candidates.json", [])
    assert candidates[0]["render_dirty"] is True
    assert candidates[2]["render_dirty"] is False


def test_batch_render_keeps_original_indices_after_ranking(tmp_path, monkeypatch):
    items = render_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline, "_ensure_shorts_candidates_current", lambda *_: (items, []))
    result = pipeline.render_shorts_candidates(tmp_path, {
        "shorts_count": 2, "shorts_burn_subtitles": False, "shorts_vertical_reframe": False,
        "shorts_trim_silence": False,
    }, pipeline.JobLogger(tmp_path))
    rows = {row["index"]: row for row in result["rendered"]}
    assert rows[1]["title"] == "first weak"
    assert rows[2]["title"] == "second strong"


def test_failed_single_render_remains_dirty(tmp_path, monkeypatch):
    render_fixture(tmp_path, monkeypatch, fail=True)
    result = pipeline.render_shorts_candidates(tmp_path, {
        "shorts_count": 1, "shorts_burn_subtitles": False, "shorts_vertical_reframe": False,
        "shorts_trim_silence": False,
    }, pipeline.JobLogger(tmp_path), only_indexes={1})
    assert result["failed"][0]["index"] == 1
    assert read_json(tmp_path / "status.json", {})["state"] == "error"
    assert read_json(tmp_path / "content_factory/shorts_candidates.json", [])[0]["render_dirty"] is True


def test_output_revision_changes_when_same_path_is_replaced(tmp_path):
    output = tmp_path / "outputs/shorts/short_01.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"first")
    first_mtime_ns = 1_700_000_000_000_000_000
    os.utime(output, ns=(first_mtime_ns, first_mtime_ns))
    before = pipeline.list_output_files(tmp_path)[0]
    replacement = output.with_suffix(".pending.mp4")
    replacement.write_bytes(b"second")
    second_mtime_ns = first_mtime_ns + 2_000_000_000
    os.utime(replacement, ns=(second_mtime_ns, second_mtime_ns))
    replacement.replace(output)
    after = pipeline.list_output_files(tmp_path)[0]
    assert before["revision"] != after["revision"]
    assert after["modified_at"] == output.stat().st_mtime


def test_batch_count_removes_outputs_outside_selected_original_indices(tmp_path, monkeypatch):
    items = render_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline, "_ensure_shorts_candidates_current", lambda *_: (items, []))
    result = pipeline.render_shorts_candidates(tmp_path, {
        "shorts_count": 1, "shorts_burn_subtitles": False, "shorts_vertical_reframe": False,
        "shorts_trim_silence": False,
    }, pipeline.JobLogger(tmp_path))
    assert [row["index"] for row in result["rendered"]] == [2]
    assert not (tmp_path / "outputs/shorts/short_01.mp4").exists()


def test_empty_caption_render_removes_obsolete_sidecars_without_asr(tmp_path, monkeypatch):
    render_fixture(tmp_path, monkeypatch)
    sidecar = tmp_path / "outputs/shorts/short_03.srt"
    sidecar.write_text("old captions", encoding="utf-8")
    (tmp_path / ".short_03.ass").write_text("captions from interrupted render", encoding="utf-8")
    def unexpected(*_, **__):
        pytest.fail("manual empty captions must not invoke ASR")
    monkeypatch.setattr(pipeline, "_shorts_refined_transcript_bounded", unexpected)
    result = pipeline.render_shorts_candidates(tmp_path, {
        "shorts_count": 1, "shorts_burn_subtitles": True, "shorts_vertical_reframe": False,
        "shorts_dynamic_captions": False, "shorts_hook_title_enabled": False,
    }, pipeline.JobLogger(tmp_path), only_indexes={3})
    row = next(row for row in result["rendered"] if row["index"] == 3)
    assert row["subtitle_cues"] == 0 and row["subtitle_burned"] is False
    assert not sidecar.exists()


def test_missing_source_duration_cache_does_not_probe_during_save(editor_project, monkeypatch):
    project, client = editor_project
    (project / "source_duration_cache.json").unlink()
    def unexpected(*_, **__):
        pytest.fail("save must not probe the VOD")
    monkeypatch.setattr(pipeline, "video_duration", unexpected)
    response = client.put("/api/projects/editor/shorts/1", json=edit_payload())
    assert response.status_code == 200


def test_single_render_response_returns_persisted_candidate(editor_project, monkeypatch):
    project, client = editor_project
    # Isolate worker execution while exercising the real route and persistence.
    # Admission refusal itself is covered with real start_background_job above.
    def admitted(_project_id, _title, _task, *, kind, prepare):
        prepare(project)
        return {"started": True, "job_id": "test-job"}
    monkeypatch.setattr(api, "start_background_job", admitted)
    response = client.post("/api/projects/editor/shorts/2/render", json=edit_payload(caption_text="Исправленная фраза"))
    assert response.status_code == 200
    assert response.json()["started"] is True
    candidate = response.json()["candidate"]
    saved = read_json(project / "content_factory/shorts_candidates.json", [])
    assert candidate == saved[1]
    assert saved[0]["title"] == "first"
    assert candidate["caption_text"] == "Исправленная фраза" and candidate["render_dirty"] is True


def test_omitted_caption_restores_automatic_mode(editor_project):
    project, client = editor_project
    payload = edit_payload()
    payload.pop("caption_text")
    response = client.put("/api/projects/editor/shorts/1", json=payload)
    assert response.status_code == 200
    assert "caption_text" not in read_json(project / "content_factory/shorts_candidates.json", [])[0]


def test_edit_still_caps_at_180_seconds_with_invalid_legacy_setting(editor_project):
    project, client = editor_project
    write_json(project / "project.json", {"id": "editor", "settings": {"shorts_max_seconds": 999}})
    response = client.put("/api/projects/editor/shorts/1", json=edit_payload(start=0, end=181))
    assert response.status_code == 422


@pytest.mark.parametrize("index,expected", [(0, 422), (3, 404)])
def test_save_missing_candidate_returns_actionable_error(editor_project, index, expected):
    project, client = editor_project
    before = (project / "content_factory/shorts_candidates.json").read_bytes()
    response = client.put(f"/api/projects/editor/shorts/{index}", json=edit_payload())
    assert response.status_code == expected
    assert (project / "content_factory/shorts_candidates.json").read_bytes() == before


def test_explicit_batch_bounds_are_not_trimmed_or_rejected_by_generation_minimum():
    candidates, rejected = pipeline.normalize_shorts_candidates(
        [{"start":2.125,"end":3.625,"title":"edited","bounds_edited":True}],
        100, limit=1, min_seconds=3, max_seconds=60,
    )
    assert not rejected and len(candidates) == 1
    assert (candidates[0]["start"],candidates[0]["end"]) == (2.125,3.625)


def test_explicit_batch_bounds_over_new_limit_are_rejected_not_silently_cut():
    candidates, rejected = pipeline.normalize_shorts_candidates(
        [{"start":2,"end":55,"title":"edited","bounds_edited":True}],
        100, limit=1, min_seconds=3, max_seconds=30,
    )
    assert not candidates
    assert rejected[0]["reason"] == "invalid_edited_bounds"


def test_reselection_preserves_saved_title_and_trim_of_same_source(editor_project, monkeypatch):
    project, client = editor_project
    original = read_json(project / 'content_factory/shorts_candidates.json', [])[0]
    response = client.put('/api/projects/editor/shorts/1', json=edit_payload())
    assert response.status_code == 200
    write_json(project / 'shorts_quality_plan.json', {'fingerprint':'old'})
    monkeypatch.setattr(pipeline, '_shorts_selection_fingerprint', lambda *_: 'new')
    monkeypatch.setattr(pipeline, 'build_shorts_quality_candidates', lambda *_, **__: ([original], []))
    selected, _ = pipeline._ensure_shorts_candidates_current(project, {'shorts_count':1})
    assert selected[0]['title'] == 'Новый момент'
    assert (selected[0]['start'], selected[0]['end']) == (2.125, 25.875)
    assert selected[0]['render_dirty'] is True
