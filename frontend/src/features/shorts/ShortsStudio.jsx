import { useEffect, useRef, useState } from 'react'
import { Search, Scissors, Download, Play, Save, RotateCcw, Settings2, Check, Clock3, RefreshCw, ChevronRight } from 'lucide-react'
import { formatShortTime, parseShortTime, shortPayload, shortIdentity, outputUrl, REFRAME_CHOICES, REFRAME_HELP, reframeResult } from './shorts.js'

const storageKey = id => `highlightStudio.shortsDrafts.v1.${id}`
function restore(id) {
  try { const value = JSON.parse(localStorage.getItem(storageKey(id)) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} } catch { return {} }
}

export default function ShortsStudio({ projectId, candidates = [], outputs = [], settings = {}, status = {}, busy = false,
  sourceDuration = 0, canGenerate = true, authQuery = () => '', onSave, onRender, onGenerate, onRefresh, onOpenTasks,
  settingsPanel, diagnostics }) {
  const [selected, setSelected] = useState(1)
  const [search, setSearch] = useState('')
  const [drafts, setDrafts] = useState(() => restore(projectId))
  const [saved, setSaved] = useState({})
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState(null)
  const [previewMode, setPreviewMode] = useState('result')
  const [guides, setGuides] = useState(false)
  const [mediaError, setMediaError] = useState(false)
  const [storageError, setStorageError] = useState(false)
  const [confirmBatch, setConfirmBatch] = useState(false)
  const videoRef = useRef(null)
  const submitRef = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  useEffect(() => {
    try {localStorage.setItem(storageKey(projectId), JSON.stringify(drafts));setStorageError(false)} catch {setStorageError(true)}
  }, [drafts, projectId])
  // A dashboard poll must not restore old form values after a successful save.
  // Drop the short-lived acknowledgement once the server reports that edit.
  useEffect(() => {
    setSaved(previous => {
      let next = previous
      for (const [key, value] of Object.entries(previous)) {
        if (shortIdentity(candidates[Number(key)-1]) === shortIdentity(value)) {
          if (next === previous) next = {...previous}
          delete next[key]
        }
      }
      return next
    })
  }, [candidates])
  useEffect(() => { if (selected > candidates.length && candidates.length) setSelected(1) }, [candidates.length, selected])

  const index = Math.min(selected, candidates.length || 1)
  const candidate = saved[index] || candidates[index - 1]
  const record = drafts[index]
  const hasDraft = Boolean(record && record.identity === shortIdentity(candidate))
  const staleDraft = Boolean(record && !hasDraft)
  const patch = hasDraft ? record.patch : {}
  const draft = candidate ? {...candidate, start:formatShortTime(candidate.start), end:formatShortTime(candidate.end), ...patch} : null
  const edited = hasDraft && Object.keys(patch).length > 0
  const output = outputs.find(item => item.name === `short_${String(index).padStart(2,'0')}.mp4`)
  const resultUrl = outputUrl(output, authQuery)
  const reframeInfo = reframeResult(output?.reframe_report)
  const isSource = previewMode === 'source' || !resultUrl
  const sourcePath = `/api/projects/${encodeURIComponent(projectId)}/source-video`
  const mediaUrl = isSource ? `${sourcePath}${authQuery(sourcePath)}` : resultUrl
  const locked = busy || Boolean(pending)
  const needsRender = edited || Boolean(candidate?.render_dirty)
  const start = parseShortTime(draft?.start)
  const end = parseShortTime(draft?.end)
  let payload, validation = ''
  if (draft) {try {payload = shortPayload(draft,settings,sourceDuration)} catch (error) {validation = error.message}}
  const localCount = Object.entries(drafts).filter(([key,row]) => row?.identity === shortIdentity(saved[key] || candidates[Number(key)-1])).length
  const visible = candidates.map((row,offset) => ({row,index:offset+1})).filter(({row,index:i}) => `${i} ${row.title}`.toLowerCase().includes(search.toLowerCase()))
  const jobState = String(status.state || '')
  const shortJob = /short/i.test(`${status.job_kind || ''} ${status.stage || ''} ${status.message || ''}`)

  useEffect(() => {setMediaError(false)}, [mediaUrl,index])
  function change(key,value) {
    if (!candidate) return
    setDrafts(previous => ({...previous,[index]:{identity:shortIdentity(candidate),patch:{...(previous[index]?.identity === shortIdentity(candidate) ? previous[index].patch : {}),[key]:value}}}))
    setMessage(null)
  }
  function discard() {setDrafts(previous => {const next={...previous};delete next[index];return next});setMessage(null)}
  function select(i) {videoRef.current?.pause();setSelected(i);setMessage(null)}
  function stamp(key) {const value = videoRef.current?.currentTime; if (Number.isFinite(value)) change(key,formatShortTime(value))}
  async function submit(kind) {
    if (submitRef.current || locked || !payload) return
    submitRef.current = true;setPending(kind);setMessage(null)
    const submittedIndex = index
    try {
      const result = await (kind === 'save' ? onSave(submittedIndex,payload) : onRender(submittedIndex,payload))
      if (!mounted.current) return
      const next = result?.candidate || result || {...candidate,...payload,render_dirty:true}
      setSaved(previous => ({...previous,[submittedIndex]:next}))
      setDrafts(previous => {const copy={...previous};delete copy[submittedIndex];return copy})
      setMessage({type:'ok',text:kind === 'save' ? 'Правки сохранены в проекте. Собери ролик, чтобы обновить видео.' : 'Сборка запущена. Готовое видео обновится здесь.'})
    } catch (error) {if(mounted.current) setMessage({type:'error',text:error.message || 'Не удалось выполнить действие. Черновик сохранён.'})}
    finally {submitRef.current=false;if(mounted.current)setPending('')}
  }
  async function generate() {
    if (submitRef.current || locked || localCount) return
    if (outputs.length && !confirmBatch) {setConfirmBatch(true);return}
    submitRef.current=true;setPending('batch');setMessage(null);setConfirmBatch(false)
    try {await onGenerate()} catch(error) {if(mounted.current)setMessage({type:'error',text:error.message})}
    finally {submitRef.current=false;if(mounted.current)setPending('')}
  }

  return <section className="ssStudio" aria-label="Shorts Studio">
    <header className="ssHeader"><div><span className="ssEyebrow">SHORTS STUDIO</span><h2>Один момент. Один ролик.</h2><p>Выбери фрагмент, доведи до финала и скачай.</p></div><div className="ssTotals"><span><b>{candidates.length}</b> фрагментов</span><span><b>{outputs.length}</b> файлов</span><span>9:16 · 1080p</span></div></header>
    <details className="ssSettings" open={candidates.length ? undefined : true}><summary><Settings2 size={17}/><b>Настройки создания</b><span>{settings.shorts_count || 5} роликов · {settings.shorts_burn_subtitles ? 'с субтитрами' : 'без субтитров'}<ChevronRight size={16}/></span></summary>
      <fieldset disabled={locked}>{settingsPanel}</fieldset>
      <div className="ssBatch"><p>{localCount ? 'Сначала сохрани или отмени правки в роликах, чтобы собрать всю подборку.' : 'Изменения общих настроек применятся при следующей сборке.'}</p><button type="button" onClick={generate} disabled={locked || !canGenerate || localCount > 0}><Scissors size={16}/>{pending === 'batch' ? 'Запускаем…' : 'Собрать подборку'}</button></div>
      {confirmBatch && <div className="ssConfirm" role="alert"><span>Готовые файлы подборки обновятся. Продолжить?</span><button type="button" onClick={generate} disabled={locked}>Да, собрать</button><button type="button" onClick={()=>setConfirmBatch(false)}>Отмена</button></div>}
    </details>
    {(busy || (shortJob && ['error','cancelled'].includes(jobState))) && <div className={`ssNotice ${jobState === 'error' ? 'ssError' : ''}`}><Clock3 size={17}/><span>{busy ? 'Задача выполняется. Сохранение и сборка доступны после её завершения.' : status.message || 'Сборка остановлена.'}</span><button type="button" onClick={onOpenTasks || onRefresh}>{onOpenTasks ? 'Открыть задачи' : <><RefreshCw size={15}/> Обновить</>}</button></div>}
    {message && <div role={message.type === 'error' ? 'alert' : 'status'} className={`ssNotice ${message.type === 'error' ? 'ssError' : ''}`}>{message.type === 'ok' && <Check size={17}/>} {message.text}</div>}
    {storageError && <div role="alert" className="ssNotice ssError">Браузер не сохранил черновик. Сохрани правки в проекте перед закрытием вкладки.</div>}
    {candidates.length ? <div className="ssWorkspace">
      <aside className="ssSidebar"><div className="ssListHead"><b>Фрагменты <span>{candidates.length}</span></b><small>{localCount ? `${localCount} с правками` : 'Выбери ролик'}</small></div><label className="ssSearch"><Search size={16}/><input aria-label="Найти ролик" placeholder="Найти по названию…" value={search} onChange={e=>setSearch(e.target.value)}/></label>
        <nav aria-label="Список Shorts" className="ssClipList">{visible.map(({row,index:i}) => {
          const hasOutput=outputs.some(file=>file.name===`short_${String(i).padStart(2,'0')}.mp4`)
          const changed = drafts[i]?.identity === shortIdentity(saved[i] || row)
          const dirty=changed || (saved[i] || row).render_dirty
          return <button type="button" className={`ssClip ${index===i?'isSelected':''}`} key={i} aria-label={`Выбрать ролик ${i}: ${row.title}`} aria-current={index===i?'true':undefined} onClick={()=>select(i)}><span className="ssClipNumber">{String(i).padStart(2,'0')}</span><span className="ssClipInfo"><b>{(changed && drafts[i]?.patch?.title) || (saved[i] || row).title || `Ролик ${i}`}</b><small>{Math.max(0,Number(row.end)-Number(row.start)).toFixed(1)} сек · <em className={dirty?'ssAmber':hasOutput?'ssGreen':''}>{changed?'Черновик':dirty?'Нужна сборка':hasOutput?'Файл готов':'Не собран'}</em></small></span><ChevronRight size={15}/></button>
        })}{!visible.length && <p className="ssEmptySearch">Ничего не найдено. Измени запрос.</p>}</nav>
      </aside>
      {draft && <section className="ssEditor" aria-label={`Редактор ролика ${index}`}>
        <div className="ssEditorHead"><div><span className="ssEyebrow">РОЛИК {String(index).padStart(2,'0')}</span><h3>{candidate.title || `Ролик ${index}`}</h3></div><span className={`ssPill ${needsRender?'ssAmber':''}`}>{edited?'Есть правки':candidate.render_dirty?'Нужна сборка':output?'Файл готов':'Не собран'}</span></div>
        {staleDraft && <div className="ssNotice ssError">Подборка изменилась, найден черновик предыдущего фрагмента. <button type="button" onClick={()=>{const blob=new Blob([JSON.stringify(record,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`short-${index}-draft.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}}>Скачать черновик</button><button type="button" onClick={discard}>Убрать</button></div>}
        <div className="ssEditorBody"><div className="ssPreviewPane">
          <div className="ssTabs" role="group" aria-label="Режим просмотра"><button type="button" aria-pressed={!isSource} disabled={!output} onClick={()=>setPreviewMode('result')}>Результат</button><button type="button" aria-pressed={isSource} onClick={()=>setPreviewMode('source')}>Исходник</button></div>
          <div className={`ssPlayer ${isSource?'ssSource':''}`}>
            <video key={`${index}-${mediaUrl}`} ref={videoRef} src={mediaUrl} controls playsInline preload="metadata" aria-label={isSource?'Исходный фрагмент':'Готовый Short'}
              onLoadedMetadata={()=>{if(isSource && videoRef.current && start!==null)videoRef.current.currentTime=start}}
              onPlay={()=>{const v=videoRef.current;if(isSource && v && start!==null && end!==null && (v.currentTime<start || v.currentTime>=end)) v.currentTime=start}}
              onTimeUpdate={()=>{const v=videoRef.current;if(isSource && v && !v.paused && end!==null && v.currentTime>=end)v.pause()}}
              onError={()=>setMediaError(true)}/>
            {!isSource && guides && <div className="ssGuides" aria-hidden="true"><span>Зона субтитров</span></div>}
          </div>
          {mediaError && <p className="ssNotice ssError">Видео недоступно для просмотра. Проверь исходный файл или скачай готовый ролик.</p>}
          <p className="ssPreviewNote">{isSource ? 'Исходное видео до кадрирования и субтитров. Воспроизведение ограничено границами фрагмента.' : needsRender ? 'Показана предыдущая сборка. Правки появятся после пересборки.' : 'Готовый файл. Именно это видео будет скачано.'}</p>
          {!isSource && reframeInfo && <div className={`ssReframeReport ${reframeInfo.fallback?'ssFallback':''}`} aria-label="Кадрирование готового файла"><b>{needsRender ? 'Кадрирование предыдущей сборки' : 'Кадрирование готового файла'}</b><dl><dt>Запрошено</dt><dd>{reframeInfo.requested}</dd><dt>Применено</dt><dd>{reframeInfo.applied}</dd></dl>{reframeInfo.note && <p>{reframeInfo.note}</p>}</div>}
          {!isSource && output && !reframeInfo && <p className="ssHelp">Для этой старой сборки нет отчёта о кадрировании. Он появится после пересборки.</p>}
          {!isSource && <label className="ssCheck"><input type="checkbox" checked={guides} onChange={e=>setGuides(e.target.checked)}/> Показать безопасную зону (не входит в MP4)</label>}
          {resultUrl && <a className="ssDownload" href={resultUrl} download={output.name}><Download size={17}/>Скачать {needsRender?'предыдущую версию':'MP4'}<small>{output.size_mb} МБ</small></a>}
        </div>
        <div className="ssForm"><fieldset disabled={locked}>
          <label><span>Название ролика</span><input aria-label="Название ролика" value={draft.title || ''} maxLength={180} onChange={e=>change('title',e.target.value)}/></label>
          <div className="ssSectionTitle"><b>Границы фрагмента</b><span>{start!==null && end!==null && end>start ? `${(end-start).toFixed(2)} сек` : '—'}</span></div>
          <div className="ssTimeFields"><label><span>Начало</span><input aria-label="Начало фрагмента" value={draft.start} placeholder="00:00:00.00" onChange={e=>change('start',e.target.value)} onBlur={()=>{if(start!==null && hasDraft && 'start' in patch)change('start',formatShortTime(start))}}/>{isSource && <button type="button" onClick={()=>stamp('start')}>Начало здесь</button>}</label><label><span>Конец</span><input aria-label="Конец фрагмента" value={draft.end} placeholder="00:00:00.00" onChange={e=>change('end',e.target.value)} onBlur={()=>{if(end!==null && hasDraft && 'end' in patch)change('end',formatShortTime(end))}}/>{isSource && <button type="button" onClick={()=>stamp('end')}>Конец здесь</button>}</label></div>
          <small className="ssHelp">Часы:минуты:секунды или секунды. Можно вводить сотые через точку или запятую.</small>
          <label><span>Кадрирование</span><select aria-label="Кадрирование" value={draft.reframe_mode || settings.shorts_reframe_mode || 'auto'} onChange={e=>change('reframe_mode',e.target.value)}>{REFRAME_CHOICES.map(([value,label])=><option value={value} key={value}>{label}</option>)}</select></label>
          <p className="ssReframeHelp" data-testid="reframe-help">{REFRAME_HELP[draft.reframe_mode || settings.shorts_reframe_mode || 'auto']}</p>
          <small className="ssHelp">Изменение применяется кнопкой «{output ? 'Пересобрать ролик' : 'Собрать ролик'}». После завершения проверь вкладку «Результат».</small>
          {settings.shorts_vertical_reframe === false && <p className="ssNotice ssError">Вертикальная компоновка выключена в настройках: ролик сохранится в исходном формате.</p>}
          <div className="ssSectionTitle"><b>Субтитры</b><span>{settings.shorts_burn_subtitles?'Включены':'Выключены в настройках'}</span></div>
          <label><span>Текст субтитров</span><select aria-label="Режим субтитров" value={draft.caption_text != null?'manual':'auto'} onChange={e=>change('caption_text',e.target.value==='manual' ? (draft.text_preview || '') : null)}><option value="auto">Автоматически из речи</option><option value="manual">Мой текст</option></select></label>
          {draft.caption_text != null ? <label><span>Ручной текст · {String(draft.caption_text).length}/2000</span><textarea aria-label="Ручной текст субтитров" rows={5} maxLength={2000} value={draft.caption_text} onChange={e=>change('caption_text',e.target.value)}/><small className="ssHelp">Твой текст заменит распознанную речь в этом ролике. Пустой текст убирает субтитры.</small></label> : <details className="ssTranscript"><summary>Посмотреть исходную расшифровку</summary><p>{draft.text_preview || 'Расшифровка появится при подготовке субтитров.'}</p></details>}
          <details className="ssTranscript"><summary>Надпись в начале ролика</summary><label><span>Короткий заголовок</span><input aria-label="Надпись в начале" maxLength={240} value={draft.hook_text ?? draft.hook ?? ''} onChange={e=>change('hook_text',e.target.value)}/><small className="ssHelp">Используется, если заголовок включён в настройках. Если поле пустое, берётся название ролика.</small></label></details>
        </fieldset></div></div>
        {validation && <p role="alert" className="ssNotice ssError">{validation}</p>}
        <footer className="ssActions"><span>{edited?'Черновик на этом устройстве':candidate.render_dirty?'Правки сохранены в проекте':'Можно проверить и изменить ролик'}</span><div><button type="button" className="ssReset" aria-label="Отменить несохранённые правки" onClick={discard} disabled={!edited || locked}><RotateCcw size={16}/></button><button type="button" onClick={()=>submit('save')} disabled={!edited || locked || Boolean(validation)}><Save size={16}/>{pending==='save'?'Сохраняем…':'Сохранить правки'}</button><button type="button" className="ssPrimary" onClick={()=>submit('render')} disabled={locked || Boolean(validation)}><Play size={16}/>{pending==='render'?'Запускаем…':output?'Пересобрать ролик':'Собрать ролик'}</button></div></footer>
      </section>}
    </div> : <div className="ssEmpty"><Scissors size={36}/><h3>Подготовим первые Shorts</h3><p>Проверь настройки и создай подборку из найденных моментов. После сборки каждый ролик можно отредактировать отдельно.</p><button type="button" className="ssPrimary" onClick={generate} disabled={locked || !canGenerate}>{locked?'Дождись завершения задачи':'Создать подборку'}</button>{!canGenerate && <small>Сначала заверши анализ и выбери фрагменты в монтаже.</small>}</div>}
    {diagnostics && <details className="ssDiagnostics"><summary>Подробности задачи и журнал</summary>{diagnostics}</details>}
  </section>
}
