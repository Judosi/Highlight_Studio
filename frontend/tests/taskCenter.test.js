import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
const source=fs.readFileSync(new URL('../src/app/App.jsx',import.meta.url),'utf8')
function extract(name,next) {return source.slice(source.indexOf(`  async function ${name}(`),source.indexOf(`  async function ${next}(`))}
function setup(fetcher) {
 const state={statuses:[],requests:0,pending:[],errors:[]}
 const context=vm.createContext({project:null,status:{},busy:false,twitchSpeedTesting:false,twitchUrl:'https://twitch.tv/videos/123',twitchKind:'vod',twitchStart:'',twitchEnd:'',productMode:'stable',advancedToolsOpen:false,settings:{},twitchFormat:'best',twitchQuality:'best',API:'/api',
  sourceRequestRef:{current:false},setSourceRequest:value=>state.pending.push(value),
  setStatus:value=>state.statuses.push(value),setTaskCenterOpen:()=>{},
  setError:e=>state.errors.push(e),notifyError:(_,e)=>state.errors.push(e.message),responseError:async()=> 'invalid URL',
  apiFetch:async(...args)=>{state.requests++;return fetcher(...args)},
 })
 vm.runInContext(extract('twitchImport','runTwitchSpeedTest'),context)
 return {state,context}
}
test('failed Twitch creation leaves no fake queued task and can be retried',async()=>{
 const {state,context}=setup(async()=>({ok:false,status:400}))
 await context.twitchImport(true)
 assert.equal(state.statuses.length,0)
 assert.equal(context.sourceRequestRef.current,false)
 await context.twitchImport(true)
 assert.equal(state.requests,2)
})
test('double click creates only one Twitch request',async()=>{
 const finishes=[]
 const {state,context}=setup(()=>new Promise(resolve=>{finishes.push(resolve)}))
 const first=context.twitchImport(true)
 const second=context.twitchImport(true)
 const count=state.requests
 finishes.forEach(f=>f({ok:false,status:400}));await Promise.all([first,second])
 assert.equal(count,1)
 assert.equal(context.sourceRequestRef.current,false)
})
test('cancel reaches backend even when settings cannot be saved',async()=>{
 const calls=[]
 const context=vm.createContext({project:{id:'p'},API:'/api',
  captureProjectScope:()=>({}),isProjectScopeCurrent:()=>true,
  saveSettings:async()=>{throw new Error('settings busy')},
  apiFetch:async(url)=>{calls.push(url);return{ok:true}},safeJsonResponse:async()=>({}),
  refreshAll:async()=>{},setError:()=>{},notifyError:()=>{},
 })
 const start=source.indexOf('  async function runSimple('),end=source.indexOf('  function setEditModeSynced(',start)
 vm.runInContext(source.slice(start,end),context)
 await context.runSimple('cancel')
 assert.deepEqual(calls,['/api/projects/p/cancel'])
})
test('error task panel closes and active row matches current progress',async()=>{
 const {JSDOM}=await import('jsdom');const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost/'})
 for(const key of ['window','document','HTMLElement','Node','Event','MutationObserver'])globalThis[key]=dom.window[key]
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator})
 const React=await import('react');const {render,screen,fireEvent,cleanup}=await import('@testing-library/react')
 const {createServer}=await import('vite');const vite=await createServer({server:{middlewareMode:true,hmr:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',logLevel:'error'})
 const file=new URL('./task-center-fixture.generated.jsx',import.meta.url)
 const body=source.slice(source.indexOf('  function renderJobBar('),source.indexOf('  function renderProductSidebar('))
 fs.writeFileSync(file,`import React from 'react'; export default function Fixture({open,status,jobs,close}){
 const sourceRequest='',taskCenterOpen=open, busy=['running','queued','cancel_requested'].includes(status.state), progressValue=status.progress || 0;
 const project={id:'p',name:'Test'},projects=[],globalJobs=jobs,globalJobsError='',progressExpanded=false;
 const setTaskCenterOpen=close,runSimple=()=>{},setProgressExpanded=()=>{},renderPhaseRail=()=>null,renderTaskDetails=()=>null,renderRenderLivePanel=()=>null;
 const humanizePipelineStage=x=>x,formatDuration=x=>x,progressKindLabel=x=>x,jobStateLabel=x=>x,refreshGlobalJobs=()=>{},refreshAll=()=>{},loadProjectById=()=>{};
 const X=()=>null,RefreshCw=()=>null;
 ${body}
 return renderJobBar();}`)
 try {
  const {default:Fixture}=await vite.ssrLoadModule('/tests/task-center-fixture.generated.jsx')
  const props={open:true,status:{state:'error',progress:19,message:'failed'},jobs:[],close:()=>view.rerender(React.createElement(Fixture,{...props,open:false}))}
  const view=render(React.createElement(Fixture,props))
  fireEvent.click(screen.getByRole('button',{name:'Закрыть центр задач'}))
  assert.equal(screen.queryByLabelText('Центр задач'),null)
  view.rerender(React.createElement(Fixture,{...props,open:true,status:{state:'running',progress:19},jobs:[{id:'job1',project_id:'p',state:'running',progress:1,kind:'twitch-import'}]}))
  assert.match(screen.getByRole('button',{name:/p.*twitch-import/}).textContent,/19%/)
 }finally{cleanup();await vite.close();fs.unlinkSync(file);dom.window.close()}
})

test('advanced import controls can be collapsed in Pro mode',()=>{
 const renderBody=source.slice(source.indexOf('  function renderUnifiedImportStep('),source.indexOf('  function renderUnifiedStyleStep('))
 const declaration=renderBody.match(/const advancedVisible = ([^\n]+)/)[1]
 const evaluate=(productMode,advancedToolsOpen)=>vm.runInNewContext(declaration,{productMode,advancedToolsOpen})
 assert.equal(evaluate('pro',false),false)
 assert.equal(evaluate('pro',true),true)
 assert.equal(evaluate('pro',null),true)
 assert.equal(evaluate('stable',null),false)
})
