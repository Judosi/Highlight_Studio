from types import SimpleNamespace
import importlib.util
import shutil
import subprocess

import pytest
from backend.src.highlight_studio.services import pipeline
from backend.src.highlight_studio.core.utils import write_json


def test_dense_trajectory_keeps_motion_and_both_ends():
    points = [{'t': i, 'cx': .15 + .7*i/179, 'cy': .5} for i in range(180)]
    result = pipeline._smooth_face_trajectory(points)
    assert 2 < len(result) <= 36
    assert result[0]['t'] == 0 and result[-1]['t'] == 179
    assert result[-1]['cx'] - result[0]['cx'] > .5


def test_face_at_left_edge_is_not_replaced_by_center():
    assert pipeline._smooth_face_trajectory([{'t':0,'cx':0,'cy':0}])[0]['cx'] == 0
    assert pipeline._face_x_expression([{'t':0,'cx':0}]) == '0.00000'


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='FFmpeg required')
def test_narrow_portrait_face_tracking_renders(tmp_path):
    graph = pipeline._shorts_filter_complex('smart_face', reframe_plan={
        'resolved_mode':'smart_face', 'trajectory':[{'t':0,'cx':.5},{'t':1,'cx':.8}]})
    run = subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=200x640:r=2',
        '-t','0.5','-filter_complex_threads','1','-filter_complex',graph,'-map','[vout]','-f','null','-'],
        capture_output=True,text=True,timeout=30)
    assert run.returncode == 0, run.stderr


def test_output_delivers_actual_reframe_report(tmp_path):
    video = tmp_path / 'outputs/shorts/short_01.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'video')
    report = {'requested_mode':'smart_face','resolved_mode':'smart_zoom','reason':'no_face_fallback'}
    write_json(tmp_path/'shorts_render_manifest.json', {'rendered':[{
        'index':1,'path':'outputs/shorts/short_01.mp4','reframe_report':report}]})
    assert pipeline.list_output_files(tmp_path)[0].get('reframe_report') == report


def test_cancellation_is_not_reported_as_tracking_fallback(tmp_path,monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name,*a,**k: SimpleNamespace() if name=='mediapipe' else real(name,*a,**k))
    monkeypatch.setattr(pipeline,'video_content_signature',lambda *_:{})
    monkeypatch.setattr(pipeline,'video_info',lambda *_:{'width':640,'height':360})
    def cancelled(*a,**kw):
        raise pipeline.OperationCancelled('stop')
    monkeypatch.setattr(pipeline,'run_cmd',cancelled)
    with pytest.raises(pipeline.OperationCancelled):
        pipeline._prepare_short_reframe_plan(tmp_path,tmp_path/'video.mp4',0,10,'smart_face',{})


def test_current_mediapipe_can_process_frames_without_legacy_solutions(tmp_path):
    pytest.importorskip('mediapipe')
    raw = tmp_path/'frames.rgb'
    raw.write_bytes(bytes(128*128*3*2))
    assert pipeline._detect_faces_mediapipe_from_raw(raw,width=128,height=128,fps=1) == []


def test_render_persists_reframe_report(tmp_path,monkeypatch):
    from test_shorts_editor_reliability import render_fixture
    render_fixture(tmp_path,monkeypatch)
    plan = {'requested_mode':'gameplay_facecam','resolved_mode':'smart_zoom',
            'reason':'no_face_fallback','tracking':False,'detection_ratio':0}
    monkeypatch.setattr(pipeline,'_prepare_short_reframe_plan',lambda *a,**kw:plan)
    result = pipeline.render_shorts_candidates(tmp_path,{
        'shorts_count':1,'shorts_vertical_reframe':True,'shorts_burn_subtitles':False,
        'shorts_hook_title_enabled':False,'shorts_trim_silence':False,
    },pipeline.JobLogger(tmp_path),only_indexes={3})
    row=next(row for row in result['rendered'] if row['index']==3)
    assert row['reframe_report']['resolved_mode'] == 'smart_zoom'
    files=pipeline.list_output_files(tmp_path)
    assert next(f for f in files if f['name']=='short_03.mp4')['reframe_report']['reason']=='no_face_fallback'


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='FFmpeg required')
@pytest.mark.parametrize('mode', ['blur_background','smart_zoom','center_crop','fit','smart_face','gameplay_facecam'])
def test_all_layouts_render_vertical_pixels(mode):
    plan={'resolved_mode':mode,'trajectory':[{'t':0,'cx':.25},{'t':1,'cx':.75}],
          'facecam':{'x':.7,'y':.05,'w':.25,'h':.3,'position':'top'}}
    graph=pipeline._shorts_filter_complex(mode,reframe_plan=plan)
    result=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=640x360:r=2',
        '-frames:v','1','-filter_complex_threads','1','-filter_complex',graph,'-map','[vout]',
        '-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert len(result.stdout) == 1080*1920*3


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='FFmpeg required')
def test_tracking_pixels_move_but_hold_before_first_face():
    plan={'resolved_mode':'smart_face','trajectory':[{'t':1,'cx':.25},{'t':2,'cx':.75}]}
    graph=pipeline._shorts_filter_complex('smart_face',reframe_plan=plan)
    graph += ';[vout]scale=1:1,format=gray[pixel]'
    result=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        "nullsrc=s=640x360:r=2,geq=lum='255*X/W':cb=128:cr=128",'-frames:v','6',
        '-filter_complex_threads','1','-filter_complex',graph,'-map','[pixel]',
        '-f','rawvideo','-'],capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    values=list(result.stdout)
    assert len(values)==6 and abs(values[0]-values[2])<=1
    assert values[-1]-values[0]>70


@pytest.mark.parametrize('manifest', [{'rendered':None}, {'rendered':42}, []])
def test_damaged_report_does_not_hide_output_files(tmp_path,manifest):
    out=tmp_path/'outputs/shorts/short_01.mp4'
    out.parent.mkdir(parents=True)
    out.write_bytes(b'video')
    write_json(tmp_path/'shorts_render_manifest.json',manifest)
    assert pipeline.list_output_files(tmp_path)[0]['name']=='short_01.mp4'
