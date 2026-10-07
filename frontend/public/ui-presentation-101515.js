(() => {
  'use strict';
  const RELEASE = '11.2.7';
  const stageLabels = {
    preparing:'Подготавливаем видео', source:'Подготавливаем видео', download:'Скачиваем исходное видео', twitch_download:'Скачиваем исходное видео',
    whisper:'Распознаём речь', transcription:'Распознаём речь', block_ai:'Анализируем содержание стрима', micro_ai:'Уточняем лучшие моменты',
    visual_scan:'Проверяем изображение', ocr_scan:'Читаем текст и чат', audio_analysis:'Проверяем звук и реакции', render:'Собираем готовое видео', render_shorts:'Создаём Shorts'
  };
  const technicalStableLabels = new Set(['Количество Shorts','Кадрирование Shorts','Субтитры в Shorts','Динамические субтитры','Вводный заголовок','Подрезать паузы','Отсекать reconnect/replay','Качество важнее длительности','Равный шанс всему VOD','Только сильные моменты','Анализ изображения']);
  const clean = value => String(value?.textContent ?? value ?? '').replace(/\s+/g,' ').trim();
  function present(root=document){
    root.querySelectorAll('.analysisProgressValue span,.analysisPipelineMap small,.jobLine b,.stageHeader b,.taskCenterHeader b').forEach(node=>{ const k=clean(node).toLowerCase(); if(stageLabels[k]) node.textContent=stageLabels[k]; });
    const page=root.querySelector('.settingsPage');
    if(page){ const heading=[...page.querySelectorAll('.settingsControlCard h3')].find(n=>['Основной','Расширенный'].includes(clean(n))); if(heading&&clean(heading)==='Основной'){ page.querySelectorAll('.settingsGridClean label').forEach(label=>{ const h=label.querySelector('span'); const name=clean(h); if(technicalStableLabels.has(name)) label.hidden=true; if(name==='Желаемая длительность, минут') h.textContent='Ориентир по длительности, минут'; }); } }
    const review=root.querySelector('.reviewPage');
    if(review){ review.querySelectorAll('.tabSwitch button').forEach(btn=>{ const v=clean(btn); if(v.startsWith('Найденные')) btn.textContent=v.replace('Найденные','Альтернативы'); if(v.startsWith('Монтаж')) btn.textContent=v.replace(/^Монтаж/,'Итоговая нарезка'); }); review.querySelectorAll('.uMomentActions .addAction,.inspectorActions button').forEach(btn=>{ const v=clean(btn); if(v==='В монтаж') btn.textContent='В итоговую'; else if(v==='Добавить в монтаж') btn.textContent='Добавить в итоговую нарезку'; else if(v==='Уже в монтаже') btn.textContent='Уже в итоговой нарезке'; }); review.querySelectorAll('.uTimelineTrack > label').forEach(label=>{ if(clean(label)==='Монтаж') label.textContent='Итоговая нарезка'; if(clean(label)==='Кандидаты') label.textContent='Альтернативы'; }); }
    document.querySelectorAll('.sidebarFooter small,.utilityVersion').forEach(node=>{ const old=clean(node); const next=old.replace(/10\.15\.\d+/g,RELEASE); if(next!==old) node.textContent=next; });
  }
  const observer=new MutationObserver(records=>{ const root=records[0]?.target?.closest?.('.studioAppV2')||document; present(root); });
  const start=()=>{ present(document); observer.observe(document.documentElement,{subtree:true,childList:true}); };
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
})();
