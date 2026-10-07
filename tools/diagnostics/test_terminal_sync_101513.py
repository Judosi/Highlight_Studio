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
   ['highlightStudioLastProject','p1'], ['highlightStudioLastStep','analysis'],
   ['highlightStudioFormatConfirmed:p1','true'], ['highlightStudioOnboarding',JSON.stringify({completed:true})]
 ]);
 const storage={getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k),clear:()=>store.clear(),key:i=>[...store.keys()][i]??null,get length(){return store.size}};
 Object.defineProperty(window,'localStorage',{value:storage,configurable:true});
 Object.defineProperty(document,'cookie',{get(){return 'highlight_local_token=test'},set(){},configurable:true});
 window.confirm=()=>true; window.open=()=>null; window.__calls=[]; window.__dashboardCalls=0;
 const baseProject={id:'p1',name:'Terminal Hydration Regression',source_type:'local',source_video_path:'C:/x.mp4',duration_seconds:600,source_ready:true,source_readiness:{ready:true,ok:true},settings:{target_minutes:10,task_preset_label:'Сбалансированный / смысл',analysis_profile:'balanced',content_type:'Auto',edit_mode:'Сбалансированный'}};
 const response=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'content-type':'application/json'}}));
 const dashboard=()=>{
   window.__dashboardCalls += 1;
   const ready=window.__dashboardCalls >= 2;
   const project={...baseProject,freshness:{analysis_current:ready,candidates_current:ready,segments_current:ready,render_current:false}};
   return {ok:true,project,status:{state:ready?'done':'idle',progress:ready?100:0,stage:ready?'done':'idle',message:ready?'Анализ завершён':''},candidates:ready?Array.from({length:57},(_,i)=>({id:`c${i}`,candidate_id:`c${i}`,start:i*5,end:i*5+4,score:8,title:`Moment ${i}`})):[],segments:ready?[{id:'s1',candidate_id:'c1',start:5,end:9,score:8,title:'AI draft'}]:[],outputs:[],freshness:{analysis_current:ready,candidates_current:ready,render_current:false},analysis_revision:ready?'a1':'',segments_revision:ready?'r1':'',preview_status:{exists:false},project_history:{items:[]},poll_after_ms:60000,checkpoints:{},creator_pack:{}};
 };
 window.fetch=(input,opts={})=>{
   const url=String(input), method=(opts.method||'GET').toUpperCase(); window.__calls.push([url,method]);
   if(url.endsWith('/api/health')) return response({ok:true,...__RELEASE_IDENTITY__});
   if(url.endsWith('/api/auth/status')) return response({accounts_enabled:false,authenticated:true,user:null});
   if(url.endsWith('/api/projects') && method==='GET') return response([baseProject]);
   if(url.endsWith('/api/jobs')) return response([{id:'job1',project_id:'p1',kind:'one_click',state:'done',progress:100,finished_at:12345}]);
   if(url.endsWith('/api/system-check')) return response({ok:true,checks:{ffmpeg:{ok:true},ffprobe:{ok:true},faster_whisper:{ok:true},ollama:{ok:true,text_model_ok:true}},recommendations:[]});
   if(url.endsWith('/api/onboarding')) return response({ok:true,completed:true,ai_mode:'local'});
   if(url.endsWith('/api/migrations/status')) return response({ok:true,failed_count:0});
   if(url.endsWith('/api/startup-recovery')) return response({ok:true,recovered_count:0});
   if(url.endsWith('/api/license/status')) return response({ok:true,status:'trial',valid:true,days_remaining:30});
   if(url.endsWith('/api/youtube/status')) return response({dependencies_available:true,credentials_configured:false,connected:false});
   if(url.endsWith('/api/twitch/tools')) return response({ok:true,tools:{}});
   if(url.endsWith('/api/projects/p1') && method==='GET') return response(baseProject);
   if(url.endsWith('/api/projects/p1/access')) return response({role:'owner',can_view:true,can_edit:true,can_manage:true});
   if(url.endsWith('/api/projects/p1/dashboard-state')) return response(dashboard());
   if(url.endsWith('/api/projects/p1/settings')) return response(baseProject.settings);
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
    page.wait_for_timeout(1500)
    body=page.locator('body').inner_text()
    state=page.evaluate("""() => ({
      dashboardCalls: window.__dashboardCalls,
      lastStep: localStorage.getItem('highlightStudioLastStep'),
      calls: window.__calls.filter(([u])=>u.includes('/dashboard-state')).length
    })""")
    assert state['dashboardCalls'] >= 2, state
    assert '57' in body, body[:3000]
    assert 'После анализа' not in body or ('Монтаж' in body and 'Анализ завершён' in body), body[:3000]
    assert state['lastStep'] == 'analysis', state
    assert not errors, errors
    print(json.dumps({'ok':True, **state, 'manual_navigation_preserved': True, 'page_errors':errors}, ensure_ascii=False))
    browser.close()
