from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend' / 'dist'
BUNDLE = next((DIST / 'assets').glob('index-*.js')).read_text(encoding='utf-8')
PRESENTATION = (DIST / 'ui-presentation-101515.js').read_text(encoding='utf-8')
CSS = '\n'.join([
    next((DIST / 'assets').glob('index-*.css')).read_text(encoding='utf-8'),
    (DIST / 'studio-v3.css').read_text(encoding='utf-8'),
    (DIST / 'studio-final-101513.css').read_text(encoding='utf-8'),
])

MOCK = r'''() => {
 const store = new Map([
   ['highlightStudioLastProject','p1'], ['highlightStudioLastStep','review'],
   ['highlightStudioFormatConfirmed:p1','true'], ['highlightStudioOnboarding',JSON.stringify({completed:true})]
 ]);
 Object.defineProperty(window,'localStorage',{value:{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k),clear:()=>store.clear(),key:i=>[...store.keys()][i]??null,get length(){return store.size}},configurable:true});
 Object.defineProperty(document,'cookie',{get(){return 'highlight_local_token=test'},set(){},configurable:true});
 window.confirm=()=>true; window.open=()=>null; window.__calls=[];
 const p1={id:'p1',name:'Old Project Must Disappear',source_type:'local',source_video_path:'C:/old.mp4',duration_seconds:600,source_ready:true,source_readiness:{ready:true,ok:true},freshness:{analysis_current:true,candidates_current:true,segments_current:true,render_current:false},settings:{target_minutes:10,task_preset_label:'Сбалансированный / смысл',analysis_profile:'balanced',content_type:'Auto',edit_mode:'Сбалансированный'}};
 const p2={id:'p2',name:'Background Project',source_type:'local',source_video_path:'C:/b.mp4',duration_seconds:600,source_ready:true,source_readiness:{ready:true,ok:true},settings:{}};
 const candidates=[{id:'c1',candidate_id:'c1',start:10,end:20,score:9,title:'Old candidate'}];
 const segments=[{id:'s1',candidate_id:'c1',start:10,end:20,score:9,title:'Old segment'}];
 const response=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'content-type':'application/json'}}));
 const dash={ok:true,project:p1,status:{state:'done',progress:100,stage:'done',message:'Анализ завершён'},candidates,segments,outputs:[],freshness:{analysis_current:true,candidates_current:true,segments_current:true,render_current:false},analysis_revision:'a1',segments_revision:'r1',preview_status:{exists:false},project_history:{items:[]},poll_after_ms:60000,checkpoints:{},creator_pack:{}};
 window.fetch=(input,opts={})=>{
   const url=String(input), method=(opts.method||'GET').toUpperCase(); window.__calls.push([url,method]);
   if(url.endsWith('/api/health')) return response({ok:true,...__RELEASE_IDENTITY__});
   if(url.endsWith('/api/auth/status')) return response({accounts_enabled:false,authenticated:true,user:null});
   if(url.endsWith('/api/projects') && method==='GET') return response([p1,p2]);
   if(url.endsWith('/api/jobs')) return response([{id:'j2',project_id:'p2',project_name:'Background Project',kind:'analysis',state:'running',progress:42}]);
   if(url.endsWith('/api/system-check')) return response({ok:true,checks:{ffmpeg:{ok:true},ffprobe:{ok:true},faster_whisper:{ok:true},ollama:{ok:true,text_model_ok:true}},recommendations:[]});
   if(url.endsWith('/api/onboarding')) return response({ok:true,completed:true,ai_mode:'local'});
   if(url.endsWith('/api/migrations/status')) return response({ok:true,failed_count:0});
   if(url.endsWith('/api/startup-recovery')) return response({ok:true,recovered_count:0});
   if(url.endsWith('/api/license/status')) return response({ok:true,status:'trial',valid:true,days_remaining:30});
   if(url.endsWith('/api/youtube/status')) return response({dependencies_available:true,credentials_configured:false,connected:false});
   if(url.endsWith('/api/twitch/tools')) return response({ok:true,tools:{}});
   if(url.endsWith('/api/projects/p1') && method==='GET') return response(p1);
   if(url.endsWith('/api/projects/p1/access')) return response({role:'owner',can_view:true,can_edit:true,can_manage:true});
   if(url.endsWith('/api/projects/p1/dashboard-state')) return response(dash);
   if(url.endsWith('/api/projects/p1/settings')) return response(p1.settings);
   if(url.includes('/api/local/browse')) return response({path:'C:/Videos',parent:'C:/',directories:[],videos:[]});
   return response({ok:true});
 };
}'''
MOCK = MOCK.replace('__RELEASE_IDENTITY__', json.dumps(json.loads((ROOT / 'release_identity.json').read_text(encoding='utf-8'))))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1600,'height':900})
    errors=[]
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.set_content(f'<!doctype html><html><head><style>{CSS}</style></head><body><div id="root"></div></body></html>')
    page.evaluate(MOCK)
    page.add_script_tag(content=BUNDLE)
    page.add_script_tag(content=PRESENTATION)
    page.wait_for_timeout(1000)

    # Global Task Center is React-owned and can show another project's job.
    page.locator('.taskCenterButton').click()
    page.wait_for_timeout(150)
    task_text = page.locator('.taskCenter').inner_text()
    assert 'Все фоновые задачи' in task_text, task_text
    assert 'Background Project' in task_text, task_text
    assert '42%' in task_text, task_text

    # New Project must detach the old project immediately.
    page.get_by_role('button', name='Новый проект', exact=False).first.click()
    page.wait_for_timeout(250)
    body = page.locator('body').inner_text()
    state = page.evaluate("""() => ({
      lastProject: localStorage.getItem('highlightStudioLastProject'),
      lastStep: localStorage.getItem('highlightStudioLastStep')
    })""")
    assert state['lastProject'] is None, state
    assert 'Выбрать проект' in body, body[:2500]
    assert 'Old candidate' not in body, body[:2500]
    assert 'Old segment' not in body, body[:2500]
    assert ('Добавь исходное видео' in body or 'Источник' in body), body[:2500]
    assert not errors, errors
    print(json.dumps({'ok':True,'task_center_global':True,'draft_last_project':state['lastProject'],'last_step':state['lastStep'],'page_errors':errors},ensure_ascii=False))
    browser.close()
