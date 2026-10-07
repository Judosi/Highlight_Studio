import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..')
const sourceCss = path.join(root, 'frontend', 'src', 'styles', 'studio-final.css')
const publicCss = path.join(root, 'frontend', 'public', 'studio-final-101513.css')
const distCss = path.join(root, 'frontend', 'dist', 'studio-final-101513.css')
const bundlePath = path.join(root, 'frontend', 'dist', 'assets', 'index-1122-8e7b1b0d9839.js')

function replaceOnce(source, before, after, label) {
  const count = source.split(before).length - 1
  if (count !== 1) throw new Error(`${label}: expected one match, found ${count}`)
  return source.replace(before, after)
}

fs.copyFileSync(sourceCss, publicCss)
fs.copyFileSync(sourceCss, distCss)

let bundle = fs.readFileSync(bundlePath, 'utf8')

bundle = replaceOnce(
  bundle,
  '[srRev,setSrRev]=(0,y.useState)(``),[,fe]',
  '[srRev,setSrRev]=(0,y.useState)(``),[hsPending,setHsPending]=(0,y.useState)([]),[,fe]',
  'pending state',
)

bundle = replaceOnce(
  bundle,
  'ri([]),ai([]),yr(`candidates`),setHsReviewTouched101515(!1)',
  'ri([]),ai([]),setHsPending([]),yr(`candidates`),setHsReviewTouched101515(!1)',
  'project reset',
)

const addStart = bundle.indexOf('async function Ja(e){')
const addEnd = bundle.indexOf('async function Ya(e){', addStart)
if (addStart < 0 || addEnd < 0) throw new Error('candidate add function was not found')
const addReplacement = `function hsPendingKey(e,t=c?.id){return\`${'${t||`no-project`}:${St(e||{})}'}\`}function hsIsAdding(e){return hsPending.includes(hsPendingKey(e))}async function Ja(e){if(!e||qa(e))return R(\`Этот момент уже находится в итоговой нарезке.\`),!1;if(!c?.id)return R(\`Сначала открой проект.\`),!1;let t=Tt(e),n=c.id,r=C,i=srRev,a=hsPendingKey(t,n);if(hsPending.includes(a))return!1;if(Et(C,t)>=0)return R(\`Похожий фрагмент уже есть в итоговой нарезке. Измени границы существующего фрагмента вместо создания дубля.\`),!1;setHsPending(e=>e.includes(a)?e:[...e,a]),Fi({type:\`info\`,title:\`Добавляю момент…\`,message:\`Команда принята. Фрагмент сохраняется в итоговую нарезку; повторное нажатие временно заблокировано.\`});try{let e=await D(\`${'${E}'}\/projects\/${'${n}'}\/segments\/add\`,{method:\`POST\`,headers:{"Content-Type":\`application\/json\`},body:JSON.stringify({item:t,expected_revision:i||\`\`})}),o=await O(e,{});if(localStorage.getItem(\`highlightStudioLastProject\`)!==n)return!1;if(e.status===409||e.status===428)return await G(n),Fi({type:\`error\`,title:\`Монтаж изменился в другом окне\`,message:o?.detail?.message||\`Данные обновлены. Повтори добавление момента на актуальной версии монтажа.\`}),!1;if(!e.ok||o?.ok===!1)throw Error(k(o,\`HTTP ${'${e.status}'}\`));let s=Array.isArray(o?.segments)?o.segments:C;return w(s),o?.segments_revision&&setSrRev(o.segments_revision),ri(e=>[...e.slice(-29),r]),ai([]),yr(\`candidates\`),setHsReviewTouched101515(!0),Fi({type:\`success\`,title:o?.added===!1?\`Момент уже был добавлен\`:\`Добавлено в итоговую нарезку\`,message:o?.message||\`Изменение сохранено.\`}),s}catch(e){return localStorage.getItem(\`highlightStudioLastProject\`)===n&&R(\`Не удалось добавить момент: ${'${$i(e.message)}'}\`),!1}finally{setHsPending(e=>e.filter(e=>e!==a))}}`
bundle = bundle.slice(0, addStart) + addReplacement + bundle.slice(addEnd)

bundle = replaceOnce(
  bundle,
  'function Ao(){let e=J||Hi[0]||C[0],r=[[`all`,`Все`]',
  'function Ao(){let e=J||Hi[0]||C[0],activeAdding=!!(e&&hsIsAdding(e)),r=[[`all`,`Все`]',
  'active inspector pending state',
)

bundle = replaceOnce(
  bundle,
  'function f(t,n=`candidate`,r=0){let a=to(t,n),o=Lr===a,s=e&&a===i,c=mt(t,z);',
  'function f(t,n=`candidate`,r=0){let a=to(t,n),o=Lr===a,s=e&&a===i,c=mt(t,z),isAdding=n===`candidate`&&hsIsAdding(t);',
  'moment card pending state',
)

bundle = replaceOnce(
  bundle,
  'n===`candidate`?(0,j.jsx)(`button`,{className:`addAction`,disabled:qa(t),onClick:()=>Ja(t),children:qa(t)?`В монтаже`:`В монтаж`}):null',
  'n===`candidate`?(0,j.jsx)(`button`,{className:`addAction ${isAdding?`isPending`:``}`,"aria-busy":isAdding?`true`:`false`,disabled:isAdding||qa(t),onClick:()=>Ja(t),children:isAdding?`Добавляю…`:qa(t)?`В монтаже ✓`:`В монтаж`}):null',
  'moment card action',
)

bundle = replaceOnce(
  bundle,
  '(0,j.jsx)(`button`,{className:`primary`,disabled:qa(e),onClick:()=>Ja(e),children:qa(e)?`Уже в итоговой нарезке`:`Добавить в итоговую нарезку`})',
  '(0,j.jsx)(`button`,{className:`primary ${activeAdding?`isPending`:``}`,"aria-busy":activeAdding?`true`:`false`,disabled:activeAdding||qa(e),onClick:()=>Ja(e),children:activeAdding?`Добавляю и сохраняю…`:qa(e)?`Уже в итоговой нарезке ✓`:`Добавить в итоговую нарезку`})',
  'inspector action',
)

bundle = replaceOnce(
  bundle,
  'c&&(0,j.jsxs)(`span`,{className:`autosaveState`,children:[(0,j.jsx)(`i`,{}),`Все изменения сохранены`]})',
  'c&&(0,j.jsxs)(`span`,{className:`autosaveState ${hsPending.length?`saving`:``}`,"aria-live":`polite`,children:[(0,j.jsx)(`i`,{}),hsPending.length?`Сохраняю монтаж…`:`Все изменения сохранены`]})',
  'top autosave state',
)

bundle = replaceOnce(
  bundle,
  'C.length>0&&(0,j.jsxs)(`div`,{className:`reviewSelectionBar`,children:[(0,j.jsxs)(`div`,{children:[(0,j.jsxs)(`b`,{children:[C.length,` фрагментов · `,gt(Vi)]}),(0,j.jsx)(`span`,{children:`Изменения сохранены в проекте`})]}),(0,j.jsx)(`button`,{className:`primaryStrong`,onClick:()=>F(`export`),children:`Продолжить к экспорту`})]})',
  'C.length>0&&(0,j.jsxs)(`div`,{className:`reviewSelectionBar ${hsPending.length?`saving`:``}`,"aria-live":`polite`,children:[(0,j.jsxs)(`div`,{children:[(0,j.jsxs)(`b`,{children:[C.length,` фрагментов · `,gt(Vi)]}),(0,j.jsx)(`span`,{children:hsPending.length?`Сохраняю выбранный фрагмент…`:`Изменения сохранены в проекте`})]}),(0,j.jsx)(`button`,{className:`primaryStrong`,disabled:hsPending.length>0,onClick:()=>F(`export`),children:hsPending.length?`Подождите сохранения…`:`Продолжить к экспорту`})]})',
  'review save state',
)

fs.writeFileSync(bundlePath, bundle)
console.log('Synchronized production CSS and review-add feedback into the packaged bundle.')
