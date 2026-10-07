from __future__ import annotations

import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

VIEWPORTS = [(2560,1440),(1920,1080),(1600,900),(1536,864),(1440,900),(1366,768),(1280,720),(1024,768)]


def load_assets(root: Path):
    dist=root/'frontend'/'dist'
    js=next((dist/'assets').glob('index-*.js')).read_text(encoding='utf-8')
    css=(next((dist/'assets').glob('index-*.css')).read_text(encoding='utf-8')+'\n'+
         (dist/'studio-v3.css').read_text(encoding='utf-8')+'\n'+
         (dist/'studio-final-101513.css').read_text(encoding='utf-8'))
    ux=(dist/'ui-presentation-101515.js').read_text(encoding='utf-8')
    return js,css,ux

MOCK_SCRIPT = r'''
(state) => {
 const store = new Map(Object.entries(state.storage || {}));
 const storage = {
   getItem:k => store.has(k) ? String(store.get(k)) : null,
   setItem:(k,v) => store.set(k,String(v)),
   removeItem:k => store.delete(k), clear:()=>store.clear(),
   key:i => [...store.keys()][i] ?? null,
   get length(){ return store.size; }
 };
 Object.defineProperty(window,'localStorage',{value:storage, configurable:true});
 Object.defineProperty(document,'cookie',{get(){return 'highlight_local_token=test'},set(){}, configurable:true});
 window.open=()=>null; window.confirm=()=>true;
 const response=(obj,status=200)=>Promise.resolve(new Response(JSON.stringify(obj),{status,headers:{'content-type':'application/json'}}));
 const project=state.project;
 const projects=project ? [project] : [];
 const dashboard={
   ok:true, project, status:state.status || {state:'idle',progress:0},
   candidates:state.candidates || [], segments:state.segments || [], outputs:state.outputs || [],
   preview_status:{exists:false}, project_history:{items:[]}, ai_quality_audit:{score:85,label:'good'},
   poll_after_ms:60000, checkpoints:{}, creator_pack:{}, youtube_upload_report:null
 };
 window.fetch=(input,opts={})=>{
   const url=String(input); const method=(opts.method||'GET').toUpperCase();
   if(url.endsWith('/api/health')) return response({ok:true,...__RELEASE_IDENTITY__});
   if(url.endsWith('/api/auth/status')) return response({accounts_enabled:false,authenticated:true,user:null});
   if(url.endsWith('/api/projects')) return response(projects);
   if(url.endsWith('/api/twitch/tools')) return response({ok:true,tools:{}});
   if(url.endsWith('/api/system-check')) return response({ok:true,checks:{ffmpeg:{ok:true},ffprobe:{ok:true},faster_whisper:{ok:true},ollama:{ok:true,text_model_ok:true}},recommendations:[]});
   if(url.endsWith('/api/onboarding')) return response({ok:true,completed:true,ai_mode:'local'});
   if(url.endsWith('/api/migrations/status')) return response({ok:true,failed_count:0});
   if(url.endsWith('/api/startup-recovery')) return response({ok:true,recovered_count:0});
   if(url.endsWith('/api/license/status')) return response({ok:true,status:'trial',valid:true,days_remaining:30});
   if(url.endsWith('/api/youtube/status')) return response({dependencies_available:true,credentials_configured:false,connected:false});
   if(project && url.endsWith(`/api/projects/${project.id}`) && method==='GET') return response(project);
   if(project && url.endsWith(`/api/projects/${project.id}/access`)) return response({role:'owner',can_view:true,can_edit:true,can_manage:true});
   if(project && url.endsWith(`/api/projects/${project.id}/dashboard-state`)) return response(dashboard);
   if(project && url.endsWith(`/api/projects/${project.id}/settings`)) return response(project.settings||{});
   if(url.includes('/api/local/browse')) return response({path:'C:/Videos',parent:'C:/',directories:[],videos:[]});
   return response({ok:true});
 };
}
'''
MOCK_SCRIPT = MOCK_SCRIPT.replace('__RELEASE_IDENTITY__', json.dumps(json.loads((Path(__file__).resolve().parents[2] / 'release_identity.json').read_text(encoding='utf-8'))))



def project_base(long_name=False):
    return {
        'id':'p1',
        'name':('Twitch_VOD_2834556604_1800-14340_'+'VERY_LONG_PROJECT_NAME_'*5) if long_name else 'Twitch_VOD_2834556604_1800-14340.0',
        'source_type':'twitch','source_video_path':'C:/Videos/cache.mp4','duration_seconds':12540,
        'source_ready':True,'source_readiness':{'ready':True,'ok':True},
        'settings':{'target_minutes':45,'task_preset_label':'Сбалансированный','analysis_profile':'balanced','content_type':'IRL стрим','edit_mode':'Сбалансированный'}
    }

def scenarios():
    p=project_base(True)
    candidates=[{'id':i+1,'start':20+i*30,'end':35+i*30,'score':8.5,'title':f'Сильный момент {i+1}','reason':'Смешная реакция'} for i in range(12)]
    segments=[dict(candidates[i],source_candidate_key=f'{i+1}') for i in range(4)]
    downloading=dict(p)
    downloading['source_video_path']=''
    downloading['source_ready']=False
    downloading['source_readiness']={'ready':False,'ok':False}
    return {
      'projects-empty': {'storage':{'highlightStudioLastStep':'projects'}, 'project':None},
      'import-empty': {'storage':{'highlightStudioLastStep':'import'}, 'project':None},
      'twitch-downloading': {'storage':{'highlightStudioLastProject':'p1','highlightStudioLastStep':'style'}, 'project':downloading, 'status':{'state':'running','progress':17,'stage':'twitch_vod','message':'Twitch VOD Turbo: TwitchDownloaderCLI'}},
      'format': {'storage':{'highlightStudioLastProject':'p1','highlightStudioLastStep':'style','highlightStudioFormatConfirmed:p1':'false'},'project':p},
      'analysis-busy': {'storage':{'highlightStudioLastProject':'p1','highlightStudioLastStep':'analysis','highlightStudioFormatConfirmed:p1':'true'},'project':p,'status':{'state':'running','progress':38,'stage':'transcription','message':'Транскрибация chunk 5/13: сегментов 412','eta_seconds':900,'current_batch':5,'total_batches':13}},
      'review': {'storage':{'highlightStudioLastProject':'p1','highlightStudioLastStep':'review','highlightStudioFormatConfirmed:p1':'true'},'project':p,'candidates':candidates,'segments':segments},
      'export': {'storage':{'highlightStudioLastProject':'p1','highlightStudioLastStep':'export','highlightStudioFormatConfirmed:p1':'true'},'project':p,'candidates':candidates,'segments':segments,'outputs':[{'kind':'video','path':'outputs/highlight_final.mp4','name':'highlight_final.mp4'}]},
    }


def geometry(page):
    return page.evaluate('''() => {
      const vv=window.innerWidth;
      const doc=document.documentElement;
      const body=document.body;
      const visible=[...document.querySelectorAll('body *')].filter(el=>{
        const s=getComputedStyle(el), r=el.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' && r.width>1 && r.height>1;
      });
      const outside=visible.map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName,cls:el.className?.toString().slice(0,100)||'',left:r.left,right:r.right,width:r.width,txt:(el.textContent||'').trim().slice(0,80)}})
        .filter(x=>x.left < -2 || x.right > vv+2)
        .filter(x=>!String(x.cls).includes('projectSpotlightGlow'))
        .slice(0,20);
      const side=document.querySelector('.studioSidebar');
      const stage=document.querySelector('.studioStage');
      const workspace=document.querySelector('.studioWorkspace');
      const top=document.querySelector('.studioCommandbar');
      const unnamed=[...document.querySelectorAll('button,a[href],input,select,textarea')].filter(el=>{
        const r=el.getBoundingClientRect(), s=getComputedStyle(el); if(s.display==='none'||s.visibility==='hidden'||r.width<1||r.height<1)return false;
        const labelText=el.labels ? [...el.labels].map(x=>x.textContent||'').join(' ') : ''; const name=(el.getAttribute('aria-label')||el.getAttribute('title')||labelText||el.textContent||el.getAttribute('placeholder')||'').trim();
        return !name;
      }).map(el=>({tag:el.tagName,cls:el.className?.toString().slice(0,80)||''})).slice(0,20);
      const tiny=[...document.querySelectorAll('button,a[href]')].filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return s.display!=='none'&&s.visibility!=='hidden'&&r.width>1&&r.height>1&&(r.width<32||r.height<32)}).map(el=>{const r=el.getBoundingClientRect();return {cls:el.className?.toString().slice(0,80)||'',w:r.width,h:r.height,txt:(el.textContent||el.getAttribute('aria-label')||'').trim().slice(0,40)}}).slice(0,20);
      return {viewport:vv,docScroll:doc.scrollWidth,bodyScroll:body.scrollWidth,outside,unnamed,tiny,
        sidebar:side?side.getBoundingClientRect().width:null,stage:stage?stage.getBoundingClientRect().width:null,
        workspace:workspace?workspace.getBoundingClientRect().width:null,topScroll:top?top.scrollWidth:null,topClient:top?top.clientWidth:null,
        step:document.querySelector('.stepHero h2')?.textContent||document.querySelector('h1')?.textContent||''};
    }''')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default='.')
    ap.add_argument('--screenshots',action='store_true')
    args=ap.parse_args()
    root=Path(args.root).resolve()
    js,css,ux=load_assets(root)
    results=[]
    with sync_playwright() as pw:
      browser=pw.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
      for name,state in scenarios().items():
        for w,h in VIEWPORTS:
          page=browser.new_page(viewport={'width':w,'height':h})
          page.set_content(f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div id="root"></div></body></html>')
          page.evaluate(MOCK_SCRIPT, state)
          page.add_script_tag(content=js)
          page.add_script_tag(content=ux)
          page.wait_for_timeout(450)
          g=geometry(page)
          g.update({'scenario':name,'width':w,'height':h})
          # Exercise compact sidebar and New Project on representative desktop states.
          if name=='projects-empty' and w==1920:
            try:
              page.get_by_role('button',name='Свернуть меню').click()
              page.wait_for_timeout(100)
              g['compactSidebar']=page.locator('.studioSidebar').bounding_box()['width']
              page.get_by_role('button',name='Развернуть меню').click()
              page.get_by_role('button',name='Новый проект').first.click()
              page.wait_for_timeout(150)
              g['newProjectOpened']=page.locator('.sourcePage, .sourceLayout').count()>0 or 'Добав' in (page.locator('body').inner_text() or '')
            except Exception as e:
                g['interactionError']=str(e)
          if name=='twitch-downloading' and w==1920:
            g['staysOnSource']=('Добавь исходное видео' in (page.locator('body').inner_text() or ''))
            g['drawerInitially']=page.locator('.taskCenter').count()
          if name=='review' and w==1920:
            try:
              tabs=page.locator('.reviewPage .tabSwitch button')
              g['reviewTabs']=[tabs.nth(i).inner_text() for i in range(tabs.count())]
              g['reviewFinalSelected']=tabs.nth(1).get_attribute('aria-selected') if tabs.count()>1 else None
            except Exception as e:
                g['reviewError']=str(e)
          if name=='analysis-busy' and w==1920:
            try:
              # Busy job should not auto-open drawer.
              g['drawerInitially']=page.locator('.taskCenter').count()
              page.locator('.taskCenterButton').click()
              page.wait_for_timeout(100)
              g['drawerAfterClick']=page.locator('.taskCenter').count()
              if g['drawerAfterClick']:
                box=page.locator('.taskCenter').bounding_box()
                g['drawerBox']=box
                g['drawerScroll']={'client':page.locator('.taskCenter').evaluate('(e)=>e.clientWidth'),'scroll':page.locator('.taskCenter').evaluate('(e)=>e.scrollWidth')}
            except Exception as e:
                g['taskError']=str(e)
          if args.screenshots and w==1920 and name in {'format','analysis-busy','review'}:
            page.screenshot(path=str(root/f'audit_{name}.png'), full_page=True)
          results.append(g)
          page.close()
      browser.close()
    failures=[]
    for r in results:
      if r['docScroll']>r['viewport']+2 or r['bodyScroll']>r['viewport']+2 or r['outside'] or r['unnamed']:
        failures.append(r)
    summary={'cases':len(results),'overflow_failures':len(failures),'failures':failures,'interaction':[r for r in results if 'compactSidebar' in r or 'drawerInitially' in r or 'staysOnSource' in r]}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 1 if failures else 0

if __name__=='__main__':
    raise SystemExit(main())
