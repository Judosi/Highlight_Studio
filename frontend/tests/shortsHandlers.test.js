import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
const source=fs.readFileSync(new URL('../src/app/App.jsx',import.meta.url),'utf8')
const handler=source.slice(source.indexOf('  async function submitShort('),source.indexOf('  async function runOneClickPipeline('))
function setup(response){
 const state={order:[],current:true,factory:{shorts:[{title:'old',start:0,end:10}]},status:null}
 const context=vm.createContext({project:{id:'p'},busy:false,shortRequestRef:{current:false},API:'/api',factory:state.factory,
  captureProjectScope:()=>({}),isProjectScopeCurrent:()=>state.current,
  saveSettings:async()=>{state.order.push('settings')},apiFetch:async(url,options)=>{state.order.push({url,options});return typeof response==='function'?response():response},
  safeJsonResponse:async r=>r.payload,errorFromPayload:(d,fallback)=>d.message || fallback,
  setTaskCenterOpen:()=>{},setFactory:f=>{state.factory=f(state.factory)},setStatus:s=>{state.status=s},refreshAll:async()=>{},
 })
 vm.runInContext(handler,context)
 return {state,context}
}
test('single render saves settings first and sends exact clip data',async()=>{
 const candidate={title:'edited',start:9803.56,end:9832.63,render_dirty:true}
 const {state,context}=setup({ok:true,payload:{started:true,candidate}})
 await context.submitShort(1,candidate,true)
 assert.equal(state.order[0],'settings')
 assert.equal(state.order[1].url,'/api/projects/p/shorts/1/render')
 assert.equal(JSON.parse(state.order[1].options.body).start,9803.56)
 assert.equal(state.factory.shorts[0],candidate)
 assert.equal(state.status.state,'queued')
})
test('admission refusal does not claim render started or discard edits',async()=>{
 const {state,context}=setup({ok:true,payload:{started:false,message:'Задача уже запущена'}})
 await assert.rejects(context.submitShort(1,{title:'edited'},true),/Задача уже запущена/)
 assert.equal(state.factory.shorts[0].title,'old')
 assert.equal(state.status,null)
 assert.equal(context.shortRequestRef.current,false)
})
test('late save response cannot overwrite another project',async()=>{
 let resolve
 const {state,context}=setup(()=>new Promise(r=>{resolve=r}))
 const pending=context.submitShort(1,{title:'edited'},false)
 await new Promise(r=>setImmediate(r));state.current=false
 resolve({ok:true,payload:{candidate:{title:'edited'}}});await pending
 assert.equal(state.factory.shorts[0].title,'old')
 assert.equal(state.order[1].options.method,'PUT')
})
