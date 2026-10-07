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
 const project={id:'p1',name:'Review Regression',source_type:'local',source_video_path:'C:/x.mp4',duration_seconds:600,source_ready:true,source_readiness:{ready:true,ok:true},freshness:{analysis_current:true,candidates_current:true,segments_current:true,render_current:false},settings:{target_minutes:10,task_preset_label:'Сбалансированный',analysis_profile:'balanced',content_type:'Auto',edit_mode:'Сбалансированный'}};
 const cand=[
   {id:'c1',candidate_id:'c1',start:10,end:20,score:9,title:'Первый сильный момент',reason:'reaction'},
   {id:'c2',candidate_id:'c2',start:40,end:55,score:8.7,title:'Второй сильный момент',reason:'funny'}
 ];
 let seg=[{id:'s0',candidate_id:'s0',start:100,end:115,score:8,title:'AI draft',source_candidate_key:'s0'}];
 const response=(obj,status=200,headers={})=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'content-type':'application/json',...headers}}));
 const dashboard=()=>({ok:true,project,status:{state:'done',progress:100,stage:'done',message:'Анализ завершён'},candidates:cand,segments:seg,outputs:[],freshness:{analysis_current:true,render_current:false},analysis_revision:'a1',segments_revision:'r'+seg.length,preview_status:{exists:false},project_history:{items:[]},ai_quality_audit:{score:90,label:'good'},poll_after_ms:60000,checkpoints:{},creator_pack:{}});
 window.fetch=(input,opts={})=>{
   const url=String(input), method=(opts.method||'GET').toUpperCase(); window.__calls.push([url,method]);
   if(url.endsWith('/api/health')) return response({ok:true,...__RELEASE_IDENTITY__});
   if(url.endsWith('/api/auth/status')) return response({accounts_enabled:false,authenticated:true,user:null});
   if(url.endsWith('/api/projects') && method==='GET') return response([project]);
   if(url.endsWith('/api/jobs')) return response([]);
   if(url.endsWith('/api/system-check')) return response({ok:true,checks:{ffmpeg:{ok:true},ffprobe:{ok:true},faster_whisper:{ok:true},ollama:{ok:true,text_model_ok:true}},recommendations:[]});
   if(url.endsWith('/api/onboarding')) return response({ok:true,completed:true,ai_mode:'local'});
   if(url.endsWith('/api/migrations/status')) return response({ok:true,failed_count:0});
   if(url.endsWith('/api/startup-recovery')) return response({ok:true,recovered_count:0});
   if(url.endsWith('/api/license/status')) return response({ok:true,status:'trial',valid:true,days_remaining:30});
   if(url.endsWith('/api/youtube/status')) return response({dependencies_available:true,credentials_configured:false,connected:false});
   if(url.endsWith('/api/twitch/tools')) return response({ok:true,tools:{}});
   if(url.endsWith('/api/projects/p1') && method==='GET') return response(project);
   if(url.endsWith('/api/projects/p1/access')) return response({role:'owner',can_view:true,can_edit:true,can_manage:true});
   if(url.endsWith('/api/projects/p1/dashboard-state')) return response(dashboard());
   if(url.endsWith('/api/projects/p1/settings')) return response(project.settings);
   if(url.endsWith('/api/projects/p1/segments/add') && method==='POST') {
     const body=JSON.parse(opts.body||'{}'); seg=[...seg,{...body.item,source_candidate_key:body.item.source_candidate_key||'new-key'}];
     return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify({ok:true,added:true,message:'Момент добавлен в итоговую нарезку.',segments:seg,segments_revision:'r'+seg.length}),{status:200,headers:{'content-type':'application/json'}})), 500));
   }
   return response({ok:true});
 };
}'''
MOCK = MOCK.replace('__RELEASE_IDENTITY__', json.dumps(json.loads((ROOT / 'release_identity.json').read_text(encoding='utf-8'))))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1600, 'height': 900})
    errors=[]
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.set_content(f'<!doctype html><html><head><style>{CSS}</style></head><body><div id="root"></div></body></html>')
    page.evaluate(MOCK)
    page.add_script_tag(content=BUNDLE)
    page.add_script_tag(content=PRESENTATION)
    page.wait_for_timeout(1000)
    tabs=page.locator('.reviewPage .tabSwitch button')
    assert tabs.count() >= 2, page.locator('body').inner_text()[:2000]
    assert 'Итоговая нарезка' in tabs.nth(1).inner_text()
    assert tabs.nth(1).get_attribute('aria-selected') == 'true'
    tabs.nth(0).click()
    page.wait_for_timeout(150)
    add=page.locator('.reviewPage .uMomentCard .addAction').first
    assert add.count()==1
    add.click()
    page.wait_for_timeout(60)
    assert add.is_disabled()
    assert 'Добавляю' in add.inner_text()
    assert 'Сохраняю монтаж' in page.locator('.autosaveState').inner_text()
    page.wait_for_timeout(900)
    calls=page.evaluate('window.__calls')
    assert any('/api/projects/p1/segments/add' in url and method=='POST' for url,method in calls), calls
    assert 'В монтаже' in add.inner_text()
    body=page.locator('body').inner_text()
    assert "can't access lexical declaration" not in body
    assert 'Финальный монтаж не сохранился' not in body
    assert not errors, errors
    print(json.dumps({'ok':True,'tabs':[tabs.nth(i).inner_text() for i in range(tabs.count())],'segments_add_calls':sum('/segments/add' in u for u,m in calls),'page_errors':errors},ensure_ascii=False))
    browser.close()
