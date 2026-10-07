from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
ASSETS = DIST / "assets"
OLD_VERSION = "10.15.14"
VERSION = "10.15.15"
DESIGN_ID = "studio-audited-v15"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, got {count}")
    return updated


def source_bundle() -> Path:
    candidates = sorted(ASSETS.glob("index-*.js"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("frontend/dist/assets/index-*.js not found")
    return candidates[0]


def main() -> int:
    src = source_bundle()
    text = src.read_text(encoding="utf-8")

    # Release identity only; endpoint/state enums keep their original values.
    text = text.replace(OLD_VERSION, VERSION).replace("studio-audited-v13", DESIGN_ID)

    # New Project must enter a true draft/no-project context.  The historical
    # bundle used the Source screen on top of the previous React project and
    # even opened a file picker immediately.  Clear project-scoped UI data and
    # remove the selected-project marker before starting the draft.
    old_begin = "function Z(e=`local`,t=!1){_r(e),Kn(e===`twitch_live`?`live`:`vod`),F(`import`),e===`local`&&t?aa():requestAnimationFrame(()=>a.current?.focus())}"
    new_begin = "function Z(e=`local`,t=!1){localStorage.removeItem(`highlightStudioLastProject`),l(null),Ei(null),re([]),w([]),setSrRev(``),p({}),ne([]),ee(``),Ce([]),Re(null),Ue(null),$e(null),tt(null),rt(null),vt(null),bt(null),Ot(null),Nt([]),Bt(null),Ut(null),qt(null),M(null),$t(null),cn(null),un(null),mn([]),gn(null),wn({url:``,loading:!1,error:``,key:``}),Nr(null),Ir(null),Rr(null),Zr([]),$r(null),ri([]),ai([]),xr(!1),Ai(null),Mn(null),Pn(``),Wn(``),Jn(``),Xn(``),Rn(!1),Hn(``),Sn(null),_r(e),Kn(e===`twitch_live`?`live`:`vod`),F(`import`),e===`local`&&t?aa():e!==`local`&&requestAnimationFrame(()=>s.current?.focus())}"
    text = replace_once(text, old_begin, new_begin, "new-project draft")
    # All product-level New Project entry points should first show source choice.
    text = text.replace("Z(`local`,!0)", "Z(`local`,!1)")

    # Project access: a late access response from A must not paint permissions on B.
    old_access = "D(`${E}/projects/${c.id}/access`).then(async e=>{e.ok&&Ei(await O(e,null))}).catch(()=>{})"
    new_access = "(()=>{let e=c.id,t=new AbortController;return D(`${E}/projects/${e}/access`,{signal:t.signal}).then(async n=>{if(!n.ok||localStorage.getItem(`highlightStudioLastProject`)!==e)return;let r=await O(n,null);localStorage.getItem(`highlightStudioLastProject`)===e&&Ei(r)}).catch(n=>{n?.name!==`AbortError`&&localStorage.getItem(`highlightStudioLastProject`)===e&&Ei(null)}),()=>t.abort()})()"
    # This runs inside useEffect; return value of the IIFE is the cleanup function.
    text = replace_once(text, old_access, new_access, "project access stale response")

    # Hardware preset: capture project id and ignore a late A response after switch to B.
    pattern = r"async function ra\(e=`auto_balanced`\)\{(.*?)\}async function ia\(e=``\)"
    m = re.search(pattern, text, flags=re.S)
    if not m:
        raise RuntimeError("hardware preset function not found")
    body = m.group(1)
    # Replace only the project mutation tail, keep all preset definitions intact.
    old_tail = "if(delete r.label,B(r),c)try{let t=await D(`${E}/projects/${c.id}/hardware-preset`,{method:`POST`,headers:{\"Content-Type\":`application/json`},body:JSON.stringify({preset:e})});if(!t.ok)throw Error(await Qi(t));let r=await t.json();B(e=>({...e,...r.settings||{}})),l(e=>e&&{...e,settings:{...e.settings||{},...r.settings||{}}}),d(e=>e.map(e=>e.id===c.id?{...e,settings:{...e.settings||{},...r.settings||{}}}:e)),R(`${r.label||n.label} применён и сохранён. Рекомендация: сначала сделай «Тест 10 минут», потом запускай большой VOD.`)}catch(e){R(`${n.label} применён только на экране, но не сохранился: ${e.message}`)}else R(`${n.label} применён. Создай проект, чтобы сохранить на диск.`)"
    new_tail = "if(delete r.label,B(r),c){let i=c.id;try{let t=await D(`${E}/projects/${i}/hardware-preset`,{method:`POST`,headers:{\"Content-Type\":`application/json`},body:JSON.stringify({preset:e})});if(!t.ok)throw Error(await Qi(t));let r=await t.json();if(localStorage.getItem(`highlightStudioLastProject`)!==i)return;B(e=>({...e,...r.settings||{}})),l(e=>e?.id===i?{...e,settings:{...e.settings||{},...r.settings||{}}}:e),d(e=>e.map(e=>e.id===i?{...e,settings:{...e.settings||{},...r.settings||{}}}:e)),R(`${r.label||n.label} применён и сохранён. Рекомендация: сначала сделай «Тест 10 минут», потом запускай большой VOD.`)}catch(e){localStorage.getItem(`highlightStudioLastProject`)===i&&R(`${n.label} применён только на экране, но не сохранился: ${e.message}`)}}else R(`${n.label} применён. Создай проект, чтобы сохранить на диск.`)"
    if old_tail not in body:
        raise RuntimeError("hardware preset mutation tail not found")
    body = body.replace(old_tail, new_tail, 1)
    text = text[:m.start(1)] + body + text[m.end(1):]

    # Task preset: same project-switch protection.
    old_task = "async function Va(e){let t=lt[e];if(!t)return R(`Такой task-пресет не найден.`);if(B({...z,...t.settings,ai_engine:`ollama`}),c)try{let n=await D(`${E}/projects/${c.id}/task-preset`,{method:`POST`,headers:{\"Content-Type\":`application/json`},body:JSON.stringify({preset:e})});if(!n.ok)throw Error(await Qi(n));let r=await n.json();B(e=>({...e,...r.settings||{}})),l(e=>e&&{...e,settings:{...e.settings||{},...r.settings||{}}}),d(e=>e.map(e=>e.id===c.id?{...e,settings:{...e.settings||{},...r.settings||{}}}:e)),R(`Task-пресет «${r.label||t.label}» сохранён. Теперь нажми «Собрать нарезку».`)}catch(e){R(`Task-пресет применён на экране, но не сохранился: ${e.message}`)}else R(`Task-пресет «${t.label}» применён. Создай проект, чтобы сохранить его на диск.`)}"
    new_task = "async function Va(e){let t=lt[e];if(!t)return R(`Такой task-пресет не найден.`);if(B({...z,...t.settings,ai_engine:`ollama`}),c){let i=c.id;try{let n=await D(`${E}/projects/${i}/task-preset`,{method:`POST`,headers:{\"Content-Type\":`application/json`},body:JSON.stringify({preset:e})});if(!n.ok)throw Error(await Qi(n));let r=await n.json();if(localStorage.getItem(`highlightStudioLastProject`)!==i)return;B(e=>({...e,...r.settings||{}})),l(e=>e?.id===i?{...e,settings:{...e.settings||{},...r.settings||{}}}:e),d(e=>e.map(e=>e.id===i?{...e,settings:{...e.settings||{},...r.settings||{}}}:e)),R(`Task-пресет «${r.label||t.label}» сохранён. Теперь нажми «Собрать нарезку».`)}catch(e){localStorage.getItem(`highlightStudioLastProject`)===i&&R(`Task-пресет применён на экране, но не сохранился: ${e.message}`)}}else R(`Task-пресет «${t.label}» применён. Создай проект, чтобы сохранить его на диск.`)}"
    text = replace_once(text, old_task, new_task, "task preset stale response")

    # Atomic add candidate: revision-aware response, no full-array overwrite and
    # no late A response mutating B.  This also permanently avoids the old
    # Firefox TDZ path that reconstructed the whole montage client-side.
    pattern = r"async function Ja\(e\)\{.*?\}async function Ya\(e\)"
    replacement = "async function Ja(e){if(!e||qa(e))return R(`Этот момент уже находится в итоговой нарезке.`),!1;let t=Tt(e);if(Et(C,t)>=0)return R(`Похожий фрагмент уже есть в итоговой нарезке. Измени границы существующего фрагмента вместо создания дубля.`),!1;if(!c?.id)return R(`Сначала открой проект.`),!1;let n=c.id,r=C,i=srRev;try{let e=await D(`${E}/projects/${n}/segments/add`,{method:`POST`,headers:{\"Content-Type\":`application/json`},body:JSON.stringify({item:t,expected_revision:i||``})}),a=await O(e,{});if(localStorage.getItem(`highlightStudioLastProject`)!==n)return!1;if(e.status===409||e.status===428)return await G(n),Fi({type:`error`,title:`Монтаж изменился в другом окне`,message:a?.detail?.message||`Данные обновлены. Повтори добавление момента на актуальной версии монтажа.`}),!1;if(!e.ok||a?.ok===!1)throw Error(k(a,`HTTP ${e.status}`));let o=Array.isArray(a?.segments)?a.segments:C;return w(o),a?.segments_revision&&setSrRev(a.segments_revision),ri(e=>[...e.slice(-29),r]),ai([]),R(a?.added===!1?`Момент уже был добавлен.`:`Момент добавлен в итоговую нарезку.`),o}catch(e){return localStorage.getItem(`highlightStudioLastProject`)===n&&R(`Не удалось добавить момент: ${$i(e.message)}`),!1}}async function Ya(e)"
    text = replace_regex_once(text, pattern, replacement, "atomic add candidate")

    # Clear cache must not report success when Windows kept locked directories.
    pattern = r"async function io\(\)\{.*?\}async function ao\(\)"
    replacement = "async function io(){if(c&&confirm(`Очистить AI/preview/render кэш проекта? Исходное видео, сегменты и настройки останутся.`)){let e=c.id;try{let t=await D(`${E}/projects/${e}/clear-cache`,{method:`POST`}),n=await O(t,null);if(!t.ok||n?.ok===!1)throw Error(k(n,`clear-cache ${t.status}`));if(localStorage.getItem(`highlightStudioLastProject`)!==e)return;let r=n?.removed||[],i=n?.failed||[];R(i.length?`Кэш очищен частично. Удалено: ${r.join(`, `)||`ничего`}. Не удалено: ${i.map(e=>e?.name||e?.path||e).join(`, `)}`:`Кэш очищен: ${r.join(`, `)||`ничего не найдено`}`),await G(e)}catch(t){localStorage.getItem(`highlightStudioLastProject`)===e&&R(`Кэш не очистился: ${$i(t.message)}`)}}}async function ao()"
    text = replace_regex_once(text, pattern, replacement, "clear cache truthful result")

    # Result-first Review belongs to the React state machine.  The old packaged
    # bundle relied on ux-workflow-101513.js to click tabs after render.  Patch
    # the React state/effects directly instead, so there is one state owner.
    text = replace_once(
        text,
        "[vr,yr]=(0,y.useState)(`candidates`),[P,F]",
        "[vr,yr]=(0,y.useState)(`candidates`),[hsReviewTouched101515,setHsReviewTouched101515]=(0,y.useState)(!1),[P,F]",
        "review touched state",
    )
    text = replace_once(
        text,
        "(0,y.useEffect)(()=>{Nr(null),Ir(null),Rr(null),ti({start:``,end:``}),ri([]),ai([]),yr(`candidates`)},[c?.id]),(0,y.useEffect)(()=>{if(!xn||xn.persistent||xn.type===`error`)return;",
        "(0,y.useEffect)(()=>{Nr(null),Ir(null),Rr(null),ti({start:``,end:``}),ri([]),ai([]),yr(`candidates`),setHsReviewTouched101515(!1)},[c?.id]),(0,y.useEffect)(()=>{P===`review`&&!hsReviewTouched101515&&yr(C.length>0?`final`:`candidates`)},[P,c?.id,C.length,hsReviewTouched101515]),(0,y.useEffect)(()=>{if(!xn||xn.persistent||xn.type===`error`)return;",
        "review result-first effect",
    )
    text = replace_once(text, "eyebrow:`Шаг 4 из 5`,title:vr===`candidates`?`Проверь найденные моменты`:`Собери финальный монтаж`,description:`Просматривай моменты, исправляй границы и оставляй только сильные фрагменты. Горячие клавиши доступны в справке.`", "eyebrow:`Шаг 4 из 5 · проверка необязательна`,title:vr===`final`?`AI-нарезка готова`:`Альтернативные моменты`,description:vr===`final`?`AI уже собрал черновой ролик. Проверь только то, что хочешь изменить, или сразу переходи к экспорту.`:`Здесь дополнительные сильные моменты, которыми можно заменить или дополнить итоговую нарезку.`", "review header")
    text = replace_once(text, "onClick:()=>yr(`candidates`),children:[`Найденные (`,Hi.length,`)`]", "onClick:()=>{setHsReviewTouched101515(!0),yr(`candidates`)},children:[`Альтернативы (`,Hi.length,`)`]", "review candidate tab")
    text = replace_once(text, "onClick:()=>yr(`final`),children:[`Монтаж (`,C.length,`)`]", "onClick:()=>{setHsReviewTouched101515(!0),yr(`final`)},children:[`Итоговая нарезка (`,C.length,`)`]", "review final tab")
    text = replace_once(text, "`Финальные фрагменты появятся после добавления кандидатов в монтаж.`", "`AI-нарезка пока пуста. Открой «Альтернативы» и добавь подходящие моменты.`", "review final empty")
    text = replace_once(text, "`Кандидатов сейчас 0. Обнови результаты или открой диагностику, если анализ уже завершён.`", "`Альтернативные моменты появятся после анализа.`", "review candidate empty")
    text = replace_once(text, "children:qa(e)?`Уже в монтаже`:`Добавить в монтаж`", "children:qa(e)?`Уже в итоговой нарезке`:`Добавить в итоговую нарезку`", "review inspector labels")
    text = replace_once(text, "children:`Монтаж относительно исходного видео`", "children:`Итоговая нарезка относительно исходного видео`", "review timeline title")
    text = replace_once(text, "children:`Верхняя дорожка — выбранный монтаж, нижняя — все AI-кандидаты.`", "children:`Верхняя дорожка — AI-черновик с твоими правками, нижняя — альтернативные моменты.`", "review timeline description")
    text = replace_once(text, "children:[`из `,z.target_minutes||0,` мин цели`]", "children:[`из ориентира `,z.target_minutes||0,` мин`]", "review timeline target")
    text = replace_once(text, "className:`uTimelineTrack final`,children:[(0,j.jsx)(`label`,{children:`Монтаж`})", "className:`uTimelineTrack final`,children:[(0,j.jsx)(`label`,{children:`Итоговая нарезка`})", "review timeline final label")
    text = replace_once(text, "className:`uTimelineTrack candidates`,children:[(0,j.jsx)(`label`,{children:`Кандидаты`})", "className:`uTimelineTrack candidates`,children:[(0,j.jsx)(`label`,{children:`Альтернативы`})", "review timeline alternative label")

    # React-owned global jobs runtime and terminal hydration.  This replaces the
    # second poller/reload watchdog that used to live in ux-workflow-101513.js.
    text = replace_once(
        text,
        "[hsReviewTouched101515,setHsReviewTouched101515]=(0,y.useState)(!1),[P,F]",
        "[hsReviewTouched101515,setHsReviewTouched101515]=(0,y.useState)(!1),[hsGlobalJobs101515,setHsGlobalJobs101515]=(0,y.useState)([]),[hsGlobalJobsError101515,setHsGlobalJobsError101515]=(0,y.useState)(``),hsLastTerminalSync101515=(0,y.useRef)(``),[P,F]",
        "global jobs state",
    )
    review_effect = "(0,y.useEffect)(()=>{P===`review`&&!hsReviewTouched101515&&yr(C.length>0?`final`:`candidates`)},[P,c?.id,C.length,hsReviewTouched101515]),"
    jobs_effect = review_effect + "(0,y.useEffect)(()=>{let e=!1;async function t(){try{let n=await D(`${E}/jobs`);if(!n.ok)return;let r=await O(n,[]);e||(setHsGlobalJobs101515(Array.isArray(r)?r:[]),setHsGlobalJobsError101515(``))}catch(n){e||setHsGlobalJobsError101515(`Не удалось обновить фоновые задачи`)}}t();let n=setInterval(t,8e3);return()=>{e=!0,clearInterval(n)}},[]),(0,y.useEffect)(()=>{if(!c?.id||!hsGlobalJobs101515.length)return;let e=hsGlobalJobs101515.find(e=>String(e?.project_id||``)===String(c.id)&&String(e?.state||``).toLowerCase()===`done`&&[`one_click`,`analysis`,`analyze`,`ai_analyze`].includes(String(e?.kind||``).toLowerCase()));if(!e)return;let t=`${c.id}:${e.id||e.job_id||e.kind}:${e.finished_at||e.updated_at||e.progress||100}`;if(hsLastTerminalSync101515.current===t)return;hsLastTerminalSync101515.current=t;let n=c.id;G(n).then(e=>{if(!e||localStorage.getItem(`highlightStudioLastProject`)!==n)return;let t=e.freshness||e.project?.freshness||{},r=Array.isArray(e.candidates)?e.candidates.length:0;Boolean(t.analysis_current??t.candidates_current??r>0)&&(xr(!0),localStorage.setItem(`highlightStudioFormatConfirmed:${n}`,`true`))}).catch(()=>{})},[hsGlobalJobs101515,c?.id]),"
    text = replace_once(text, review_effect, jobs_effect, "global jobs terminal hydration")
    task_footer = "(0,j.jsxs)(`div`,{className:`taskCenterFooter`,children:[(0,j.jsx)(`span`,{children:c?.name||`Текущий проект`}),(0,j.jsxs)(`button`,{onClick:()=>G(),disabled:!c,children:[(0,j.jsx)(Me,{size:14}),` Обновить`]})]})]})]})}function So()"
    global_task_footer = "(0,j.jsxs)(`div`,{className:`taskCenterFooter`,children:[(0,j.jsx)(`span`,{children:c?.name||`Текущий проект`}),(0,j.jsxs)(`button`,{onClick:()=>G(),disabled:!c,children:[(0,j.jsx)(Me,{size:14}),` Обновить`]})]})]}),(0,j.jsxs)(`div`,{className:`globalTaskList`,children:[(0,j.jsxs)(`div`,{className:`globalTaskListHead`,children:[(0,j.jsx)(`b`,{children:`Все фоновые задачи`}),(0,j.jsxs)(`span`,{children:[hsGlobalJobs101515.filter(e=>[`queued`,`running`,`cancel_requested`].includes(String(e?.state||``).toLowerCase())).length,` активных`]})]}),hsGlobalJobsError101515&&(0,j.jsxs)(`p`,{className:`taskCenterError`,role:`status`,children:[hsGlobalJobsError101515,` · Показаны последние известные данные.`]}),hsGlobalJobs101515.length?hsGlobalJobs101515.slice(0,8).map(e=>{let t=Math.max(0,Math.min(100,Number(e?.progress||0)));return(0,j.jsxs)(`button`,{className:`globalTaskRow ${String(e?.state||``).toLowerCase()}`,onClick:()=>e?.project_id&&U(e.project_id),children:[(0,j.jsxs)(`span`,{className:`globalTaskCopy`,children:[(0,j.jsx)(`b`,{children:e?.project_name||e?.project_id||`Проект`}),(0,j.jsxs)(`small`,{children:[String(e?.kind||e?.title||`задача`).replaceAll(`_`,` `),` · `,String(e?.state||`задача`)]})]}),(0,j.jsxs)(`span`,{className:`globalTaskProgress`,children:[(0,j.jsx)(`i`,{style:{width:`${t}%`}}),(0,j.jsxs)(`small`,{children:[Math.round(t),`%`]})]})]},e.id||`${e.project_id}:${e.kind}`)}):(0,j.jsx)(`p`,{className:`muted`,children:`Фоновых задач пока нет.`})]})]})}function So()"
    text = replace_once(text, task_footer, global_task_footer, "global Task Center")

    # The React/Vite bundle may legitimately contain MutationObserver for
    # modulepreload and location.reload for explicit user recovery actions.  The
    # release blocker was the *second external workflow state machine*, so the
    # gate below targets that file/index composition instead of generic browser APIs.

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    out = ASSETS / f"index-101515-{digest}.js"
    out.write_text(text, encoding="utf-8")

    # Remove older JS bundles so the archive cannot accidentally serve one.
    for candidate in ASSETS.glob("index-*.js"):
        if candidate != out:
            candidate.unlink()

    # Presentation-only bridge.  It may rename/hide labels but cannot fetch,
    # navigate, reload, intercept clicks or own job/project state.
    presentation = DIST / "ui-presentation-101515.js"
    presentation.write_text("""(() => {\n  'use strict';\n  const RELEASE = '10.15.15';\n  const stageLabels = {\n    preparing:'Подготавливаем видео', source:'Подготавливаем видео', download:'Скачиваем исходное видео', twitch_download:'Скачиваем исходное видео',\n    whisper:'Распознаём речь', transcription:'Распознаём речь', block_ai:'Анализируем содержание стрима', micro_ai:'Уточняем лучшие моменты',\n    visual_scan:'Проверяем изображение', ocr_scan:'Читаем текст и чат', audio_analysis:'Проверяем звук и реакции', render:'Собираем готовое видео', render_shorts:'Создаём Shorts'\n  };\n  const technicalStableLabels = new Set(['Количество Shorts','Кадрирование Shorts','Субтитры в Shorts','Динамические субтитры','Вводный заголовок','Подрезать паузы','Отсекать reconnect/replay','Качество важнее длительности','Равный шанс всему VOD','Только сильные моменты','Анализ изображения']);\n  const clean = value => String(value?.textContent ?? value ?? '').replace(/\\s+/g,' ').trim();\n  function present(root=document){\n    root.querySelectorAll('.analysisProgressValue span,.analysisPipelineMap small,.jobLine b,.stageHeader b,.taskCenterHeader b').forEach(node=>{ const k=clean(node).toLowerCase(); if(stageLabels[k]) node.textContent=stageLabels[k]; });\n    const page=root.querySelector('.settingsPage');\n    if(page){ const heading=[...page.querySelectorAll('.settingsControlCard h3')].find(n=>['Основной','Расширенный'].includes(clean(n))); if(heading&&clean(heading)==='Основной'){ page.querySelectorAll('.settingsGridClean label').forEach(label=>{ const h=label.querySelector('span'); const name=clean(h); if(technicalStableLabels.has(name)) label.hidden=true; if(name==='Желаемая длительность, минут') h.textContent='Ориентир по длительности, минут'; }); } }\n    const review=root.querySelector('.reviewPage');\n    if(review){ review.querySelectorAll('.tabSwitch button').forEach(btn=>{ const v=clean(btn); if(v.startsWith('Найденные')) btn.textContent=v.replace('Найденные','Альтернативы'); if(v.startsWith('Монтаж')) btn.textContent=v.replace(/^Монтаж/,'Итоговая нарезка'); }); review.querySelectorAll('.uMomentActions .addAction,.inspectorActions button').forEach(btn=>{ const v=clean(btn); if(v==='В монтаж') btn.textContent='В итоговую'; else if(v==='Добавить в монтаж') btn.textContent='Добавить в итоговую нарезку'; else if(v==='Уже в монтаже') btn.textContent='Уже в итоговой нарезке'; }); review.querySelectorAll('.uTimelineTrack > label').forEach(label=>{ if(clean(label)==='Монтаж') label.textContent='Итоговая нарезка'; if(clean(label)==='Кандидаты') label.textContent='Альтернативы'; }); }\n    document.querySelectorAll('.sidebarFooter small,.utilityVersion').forEach(node=>{ const old=clean(node); const next=old.replace(/10\\.15\\.\\d+/g,RELEASE); if(next!==old) node.textContent=next; });\n  }\n  const observer=new MutationObserver(records=>{ const root=records[0]?.target?.closest?.('.studioAppV2')||document; present(root); });\n  const start=()=>{ present(document); observer.observe(document.documentElement,{subtree:true,childList:true}); };\n  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();\n})();\n""", encoding="utf-8")

    # Assert the presentation helper cannot become a hidden workflow/state layer.
    present_text = presentation.read_text(encoding="utf-8")
    forbidden = ["fetch(", "apiFetch", "location.reload", "stopImmediatePropagation", "setInterval", "sessionStorage", "/api/", ".click()"]
    hits = [token for token in forbidden if token in present_text]
    if hits:
        raise RuntimeError(f"presentation helper owns forbidden behavior: {hits}")

    index = DIST / "index.html"
    html = index.read_text(encoding="utf-8")
    html = re.sub(r'/assets/index-[^\"]+\.js', f'/assets/{out.name}', html)
    html = re.sub(r'\s*<script[^>]+src="/ux-workflow-101513\.js"[^>]*></script>', '', html)
    if "/ui-presentation-101515.js" not in html:
        html = html.replace("</body>", '  <script defer src="/ui-presentation-101515.js"></script>\n</body>')
    html = html.replace(OLD_VERSION, VERSION).replace("studio-audited-v13", DESIGN_ID)
    index.write_text(html, encoding="utf-8")

    old_compat = DIST / "ux-workflow-101513.js"
    if old_compat.exists():
        old_compat.unlink()

    release_json = DIST / "release.json"
    payload = json.loads(release_json.read_text(encoding="utf-8")) if release_json.exists() else {}
    payload.update({"version": VERSION, "design_id": DESIGN_ID, "bundle": out.name, "production_state_owner": "react"})
    release_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"patched frontend: {src.name} -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
