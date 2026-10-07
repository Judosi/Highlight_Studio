export const TASK_PRESET_MAP = {
  balanced: {
    label: 'Сбалансированный / смысл',
    badge: '✦',
    desc: 'Лучшие законченные моменты всего стрима без технических вставок и перекоса к началу.',
    tone: 'balanced',
    settings: {
      task_preset: 'balanced', task_preset_label: 'Сбалансированный / смысл', content_type: 'Auto', edit_mode: 'Сбалансированный', analysis_profile: 'balanced', target_minutes: 30, min_final_segments: 8, max_final_segments: 80,
      micro_cut_enabled: true, micro_window_seconds: 45, micro_min_seconds: 12, micro_max_seconds: 80, top_blocks_for_micro: 36,
      audio_dynamics_enabled: true, visual_scan_enabled: true, visual_scan_interval_seconds: 5, visual_scan_max_samples: 1200, ocr_enabled: true, ocr_every_n_visual_samples: 2,
      hook_first_enabled: false, hook_min_score: 8.6, dedup_enabled: true, storyline_enabled: true, refill_after_dedup_enabled: true,
      semantic_quality_guard_enabled: true, quality_first_selection_enabled: true, temporal_fairness_enabled: true,
      prompt: 'Выбери лучшие законченные моменты из ВСЕГО стрима. Сначала пойми смысл сцены: причина → событие/развитие → реакция или payoff. Сравнивай ранние и поздние моменты на равных. Не добирай длительность waiting/reconnect/intermission, рекламой, повторами, заранее записанными старыми хайлайтами или бессмысленными обрывками. Если стример вживую реагирует на чужое видео, оценивай именно его текущую реакцию и комментарий. Лучше более короткая сильная нарезка, чем 30 минут с наполнителем.'
    }
  },
  irl_funny: {
    label: 'IRL смешное',
    badge: '😂',
    desc: 'Смех, угар, мемы, донаты, абсурдные диалоги и реакции.',
    tone: 'funny',
    settings: {
      task_preset: 'irl_funny', task_preset_label: 'IRL смешное', content_type: 'IRL стрим', edit_mode: 'Только смешное', analysis_profile: 'balanced', target_minutes: 30,
      micro_cut_enabled: true, micro_window_seconds: 35, micro_min_seconds: 10, micro_max_seconds: 65, top_blocks_for_micro: 36,
      audio_dynamics_enabled: true, visual_scan_enabled: true, visual_scan_interval_seconds: 6, visual_scan_max_samples: 1000, ocr_enabled: true, ocr_every_n_visual_samples: 3,
      hook_first_enabled: false, hook_min_score: 8.4, dedup_enabled: true, storyline_enabled: true, refill_after_dedup_enabled: true,
      prompt: 'Собери IRL-нарезку с упором на смешные моменты. Высоко оценивай смех, угар, абсурдные диалоги, мемы, неожиданные реплики, донаты и чат, которые провоцируют смешную реакцию. Убирай долгую ходьбу, бытовую воду, технические паузы и обычный спокойный разговор без payoff. Каждый выбранный момент должен быть понятен зрителю без просмотра полного стрима.'
    }
  },
  irl_conflict: {
    label: 'IRL конфликт / хаос',
    badge: '⚡',
    desc: 'Споры, охрана, прохожие, неловкость, шок и сильные реакции.',
    tone: 'hot',
    settings: {
      task_preset: 'irl_conflict', task_preset_label: 'IRL конфликт / хаос', content_type: 'IRL стрим', edit_mode: 'IRL конфликт/хаос', analysis_profile: 'quality', target_minutes: 30,
      micro_cut_enabled: true, micro_window_seconds: 45, micro_min_seconds: 12, micro_max_seconds: 90, top_blocks_for_micro: 45,
      audio_dynamics_enabled: true, visual_scan_enabled: true, visual_scan_interval_seconds: 5, visual_scan_max_samples: 1200, ocr_enabled: true, ocr_every_n_visual_samples: 2,
      hook_first_enabled: false, hook_min_score: 8.5, dedup_enabled: true, storyline_enabled: true, refill_after_dedup_enabled: true,
      prompt: 'Собери IRL-нарезку с упором на конфликт, хаос и сильные реакции. Высоко оценивай споры, неловкие встречи, охрану, полицию, прохожих, донат-провокации, шок, крик, резкое изменение ситуации и моменты, где зрителю интересно узнать развязку. Оставляй контекст 5-15 секунд до реакции, если без него сцена непонятна. Убирай обычную ходьбу и спокойную болтовню.'
    }
  },
  sport: {
    label: 'Спорт / теннис',
    badge: '🎾',
    desc: 'Розыгрыши, эмоции, спорные моменты, победы, провалы.',
    tone: 'sport',
    settings: {
      task_preset: 'sport', task_preset_label: 'Спорт / теннис', content_type: 'Спорт', edit_mode: 'Сбалансированный', analysis_profile: 'balanced', target_minutes: 25,
      micro_cut_enabled: true, micro_window_seconds: 40, micro_min_seconds: 10, micro_max_seconds: 80, top_blocks_for_micro: 34,
      audio_dynamics_enabled: true, visual_scan_enabled: true, visual_scan_interval_seconds: 5, visual_scan_max_samples: 1000, ocr_enabled: true, ocr_every_n_visual_samples: 3,
      hook_first_enabled: false, hook_min_score: 8.3, dedup_enabled: true, storyline_enabled: true,
      prompt: 'Собери спортивную нарезку. Высоко оценивай яркие розыгрыши, напряжённые концовки, победы, провалы, спорные моменты, эмоции игроков/стримера, реакции чата и смешные комментарии. Убирай паузы между розыгрышами, долгую подготовку, повторяющийся счёт и спокойные разговоры без события. Итог должен смотреться как динамичный спортивный highlight.'
    }
  },
  podcast: {
    label: 'Подкаст / разговор',
    badge: '🎙️',
    desc: 'Сильные мысли, истории, спор мнений, инсайты и кликабельные фразы.',
    tone: 'podcast',
    settings: {
      task_preset: 'podcast', task_preset_label: 'Подкаст / разговор', content_type: 'Подкаст', edit_mode: 'С историей', analysis_profile: 'quality', target_minutes: 35,
      micro_cut_enabled: true, micro_window_seconds: 70, micro_min_seconds: 25, micro_max_seconds: 150, top_blocks_for_micro: 30,
      audio_dynamics_enabled: true, visual_scan_enabled: false, ocr_enabled: false, hook_first_enabled: false, hook_min_score: 8.2,
      dedup_enabled: true, storyline_enabled: true, refill_after_dedup_enabled: true,
      prompt: 'Собери разговорную нарезку для YouTube. Высоко оценивай сильные формулировки, личные истории, спор мнений, инсайты, неожиданные признания, конфликт позиции и фразы, которые можно вынести в заголовок. Убирай повторы, длинные вступления, пересказ без вывода и технические паузы. Каждый фрагмент должен быть понятен зрителю отдельно.'
    }
  },
  shorts_only: {
    label: 'Только Shorts',
    badge: '9:16',
    desc: 'Короткие вертикальные клипы с сильным hook в первые секунды.',
    tone: 'shorts',
    settings: {
      task_preset: 'shorts_only', task_preset_label: 'Только Shorts', content_type: 'Shorts', edit_mode: 'Плотно', analysis_profile: 'fast', target_minutes: 8,
      micro_cut_enabled: true, micro_window_seconds: 25, micro_min_seconds: 8, micro_max_seconds: 45, min_final_segments: 10, max_final_segments: 80, top_blocks_for_micro: 40,
      shorts_count: 10, shorts_vertical_reframe: true, shorts_reframe_mode: 'auto', shorts_burn_subtitles: true, shorts_dynamic_captions: true,
      shorts_hook_title_enabled: true, shorts_trim_silence: true, shorts_caption_max_words: 4, shorts_caption_quality: 'high', shorts_caption_font_size: 72, shorts_normalize_audio: true,
      shorts_min_seconds: 3, shorts_max_seconds: 45, shorts_crf: 22, audio_dynamics_enabled: true, visual_scan_enabled: true, visual_scan_interval_seconds: 5, visual_scan_max_samples: 900,
      ocr_enabled: true, ocr_every_n_visual_samples: 3, hook_first_enabled: false, hook_min_score: 8.0, dedup_enabled: true, storyline_enabled: false,
      prompt: 'Найди короткие моменты для Shorts. Каждый клип должен иметь сильный hook в первые 1-3 секунды: смешная реплика, шок, конфликт, резкая реакция, донат, визуальный прикол или понятный payoff. Убирай длинный контекст и спокойные разговоры. Лучше 10 коротких сильных клипов, чем один длинный слабый фрагмент.'
    }
  }
}
export const TASK_PRESET_LIST = Object.entries(TASK_PRESET_MAP).map(([id, value]) => ({ id, ...value }))
