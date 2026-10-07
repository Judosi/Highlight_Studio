import test from 'node:test'
import assert from 'node:assert/strict'
import { parseShortTime, formatShortTime, shortPayload, shortIdentity, shortIndexFromOutput } from '../src/features/shorts/shorts.js'

test('time accepts hundredths, Russian decimal commas and readable timecodes', () => {
  assert.equal(parseShortTime('9803,56'), 9803.56)
  assert.equal(parseShortTime('02:43:23.56'), 9803.56)
  assert.equal(parseShortTime('43:23,56'), 2603.56)
  assert.equal(formatShortTime(9803.56), '02:43:23.56')
  for (const value of ['', ' ', '-3', '1:60', 'abc', 'Infinity', '1e4']) assert.equal(parseShortTime(value), null)
})
test('payload validates edited range without rounding and distinguishes manual empty captions', () => {
  const base = {title: 'Ролик', start: '02:43:23.56', end: '02:43:53.63'}
  assert.deepEqual(shortPayload(base, {shorts_max_seconds:60}, 10000), {
    title:'Ролик',start:9803.56,end:9833.63,reframe_mode:'auto',hook_text:'',caption_text:null,
  })
  assert.equal(shortPayload({...base, caption_text:''},{},10000).caption_text, '')
  for (const patch of [{start:''},{end:'02:43:00'},{title:' '},{end:'02:45:00'}]) {
    assert.throws(() => shortPayload({...base,...patch},{shorts_max_seconds:60},10000))
  }
  assert.throws(() => shortPayload(base,{},9000), /исходного видео/)
})
test('draft identity detects candidate replacement but ignores render status', () => {
  const a={title:'A',start:10,end:30}
  assert.equal(shortIdentity(a), shortIdentity({...a,render_dirty:true}))
  assert.notEqual(shortIdentity(a), shortIdentity({...a,start:50}))
})

test('workspace preserves separate drafts and saves valid decimal times without rendering', {timeout:30000}, async () => {
  const { JSDOM } = await import('jsdom')
  const dom = new JSDOM('<html><body></body></html>', {url:'http://localhost/'})
  for (const key of ['window','document','localStorage','HTMLElement','Node','Event','MutationObserver']) globalThis[key] = dom.window[key]
  Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
  dom.window.HTMLMediaElement.prototype.pause = function () {}
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const React = await import('react')
  const {createServer} = await import('vite')
  const vite = await createServer({server:{middlewareMode:true,hmr:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'})
  try {
    const {render,screen,fireEvent,waitFor,cleanup} = await import('@testing-library/react')
    const {default:Studio} = await vite.ssrLoadModule('/src/features/shorts/ShortsStudio.jsx')
    const rows = [{title:'Первый',start:9803.56,end:9832.63,text_preview:'Речь',short_score:7}, {title:'Второй',start:10,end:30}]
    const calls=[]
    const props={projectId:'p1',candidates:rows,outputs:[],settings:{shorts_max_seconds:60},status:{},sourceDuration:11000,
      authQuery:()=>'',onSave:async(i,p)=>{calls.push(['save',i,p]);return p},onRender:async(i,p)=>calls.push(['render',i,p]),onGenerate:async()=>{},onRefresh:()=>{},settingsPanel:null}
    let view=render(React.createElement(Studio,props))
    assert.equal(screen.getByLabelText('Начало фрагмента').value,'02:43:23.56')
    fireEvent.change(screen.getByLabelText('Название ролика'),{target:{value:'Моя правка'}})
    fireEvent.click(screen.getByRole('button',{name:/Выбрать ролик 2/}))
    assert.equal(screen.getByLabelText('Название ролика').value,'Второй')
    fireEvent.click(screen.getByRole('button',{name:/Выбрать ролик 1/}))
    assert.equal(screen.getByLabelText('Название ролика').value,'Моя правка')
    view.unmount()
    view=render(React.createElement(Studio,props))
    assert.equal(screen.getByLabelText('Название ролика').value,'Моя правка')
    fireEvent.change(screen.getByLabelText('Конец фрагмента'),{target:{value:'0'}})
    assert.equal(screen.getByRole('button',{name:'Сохранить правки'}).disabled,true)
    fireEvent.change(screen.getByLabelText('Конец фрагмента'),{target:{value:'9832,63'}})
    fireEvent.click(screen.getByRole('button',{name:'Сохранить правки'}))
    await waitFor(()=>assert.equal(calls.length,1))
    assert.equal(calls[0][0],'save')
    assert.equal(calls[0][2].start,9803.56)
    assert.equal(calls[0][2].caption_text,null)
    await waitFor(()=>assert.equal(screen.getByRole('button',{name:'Сохранить правки'}).disabled,true))
    cleanup()
    view=render(React.createElement(Studio,{...props,projectId:'p2'}))
    assert.equal(screen.getByLabelText('Название ролика').value,'Первый')
    fireEvent.change(screen.getByLabelText('Режим субтитров'),{target:{value:'manual'}})
    fireEvent.change(screen.getByLabelText('Ручной текст субтитров'),{target:{value:''}})
    let rejectRender
    let renderCount=0
    view.rerender(React.createElement(Studio,{...props,projectId:'p2',onRender:async(i,p)=>{
      renderCount++;assert.equal(i,1);assert.equal(p.caption_text,'')
      await new Promise((_,reject)=>{rejectRender=reject})
    }}))
    fireEvent.click(screen.getByRole('button',{name:'Собрать ролик'}))
    fireEvent.click(screen.getByRole('button',{name:'Запускаем…'}))
    assert.equal(renderCount,1)
    rejectRender(new Error('Диск недоступен'))
    await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Диск недоступен/))
    assert.equal(screen.getByLabelText('Режим субтитров').value,'manual')
    assert.equal(screen.getByRole('button',{name:'Сохранить правки'}).disabled,false)
    const output={name:'short_01.mp4',url:'/video.mp4',size_mb:2,revision:'first'}
    view.rerender(React.createElement(Studio,{...props,projectId:'p2',outputs:[output]}))
    assert.match(screen.getByRole('link',{name:/Скачать предыдущую/}).href,/v=first/)
    view.rerender(React.createElement(Studio,{...props,projectId:'p2',outputs:[{...output,revision:'second',reframe_report:{requested_mode:'gameplay_facecam',resolved_mode:'smart_zoom',reason:'no_face_fallback'}}]}))
    assert.match(screen.getByRole('link',{name:/Скачать предыдущую/}).href,/v=second/)
    assert.match(screen.getByLabelText('Кадрирование готового файла').textContent, /Крупнее по центру/ )
    assert.match(screen.getByLabelText('Кадрирование готового файла').textContent, /Лицо не найдено/)
    fireEvent.change(screen.getByLabelText('Кадрирование'),{target:{value:'smart_face'}})
    assert.match(screen.getByTestId('reframe-help').textContent,/следует/)
    fireEvent.change(screen.getByLabelText('Найти ролик'),{target:{value:'Второй'}})
    assert.equal(screen.queryByRole('button',{name:/Выбрать ролик 1/}),null)
    assert.ok(screen.getByRole('button',{name:/Выбрать ролик 2/}))
    let opened=0
    view.rerender(React.createElement(Studio,{...props,projectId:'p2',busy:true,status:{state:'running',progress:37},onOpenTasks:()=>opened++}))
    assert.equal(screen.queryByRole('progressbar'),null)
    fireEvent.click(screen.getByRole('button',{name:'Открыть задачи'}))
    assert.equal(opened,1)
    cleanup()
  } finally {await vite.close();dom.window.close()}
})

test('rendered file identity is its number even when earlier candidates are missing', () => {
  assert.equal(shortIndexFromOutput({name:'short_03.mp4'}),3)
  assert.equal(shortIndexFromOutput({name:'highlight_final.mp4'}),null)
})
