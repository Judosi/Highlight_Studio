import { shortIndexFromOutput } from '../features/shorts/shorts.js'
import ShortsStudio from '../features/shorts/ShortsStudio.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { startSerialPoll } from '../lib/serialPoll.js'
import { createReviewRequestGuard } from '../lib/reviewRequestGuard.js'
import {
  Upload,
  Scissors,
  Sparkles,
  Film,
  Download,
  Wand2,
  RefreshCw,
  Zap,
  CircleHelp,
  MoreHorizontal,
  X,
  RotateCcw,
  RotateCw,
  Copy,
  Palette,
  Check,
  ChevronDown,
  Clock3,
  FolderOpen,
  ShieldCheck,
  Settings,
  Home,
  ChevronRight,
  Play,
  Radio,
  HardDrive,
  LayoutGrid,
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
} from 'lucide-react'

import {
  API,
  apiFetch,
  safeJsonResponse,
  errorFromPayload,
  authQuery,
  clearLocalSettings,
  loadSafeLocalSettings,
  persistSafeLocalSettings,
} from '../api/client'
import { TASK_PRESET_MAP, TASK_PRESET_LIST } from '../config/taskPresets'
import { DEFAULT_THEME, THEME_OPTIONS, normalizeTheme } from '../config/themes'
import { buildLocalMomentExplanation } from '../features/review/explanations'
import { secondsToTc, formatDuration, progressKindLabel } from '../shared/format'
import { normalizeNotice } from '../shared/ux'
import { candidateRejectKey, sourceCandidateKey, segmentIndexFor as findSegmentIndex, withSourceCandidateKey, findOverlapIndex, parseTrimBounds } from '../shared/review'
import {
  chooseNativeVideo,
  getDesktopInfo as readDesktopInfo,
  openNativePath,
  getNativeUpdateStatus,
  checkNativeUpdates,
  downloadNativeUpdate,
  installNativeUpdate,
  subscribeNativeUpdateStatus,
} from '../shared/desktop'
import FirstRunWizard from '../features/onboarding/FirstRunWizard'
import PaidBetaPanel from '../features/paidBeta/PaidBetaPanel'
import MassReleasePanel from '../features/release/MassReleasePanel'
import AuthScreen from '../features/auth/AuthScreen'
import TeamAccessPanel from '../features/auth/TeamAccessPanel'
import AccountAccessPanel from '../features/auth/AccountAccessPanel'

const SIDEBAR_COMPACT_KEY = 'highlightStudioSidebarCompact'
const UI_DENSITY_KEY = 'highlightStudioDensity'
const LAYOUT_PREFERENCES_VERSION_KEY = 'highlightStudioLayoutPreferencesVersion'
const LAYOUT_PREFERENCES_VERSION = 'stable-sidebar-v3'

function layoutStorage() {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch (_) {
    return null
  }
}

function persistLayoutPreference(key, value) {
  try {
    layoutStorage()?.setItem(key, value)
  } catch (_) {}
}

export function ensureLayoutPreferences(storage = layoutStorage()) {
  if (!storage) return { sidebarCompact: false, density: 'comfortable' }
  try {
    if (storage.getItem(LAYOUT_PREFERENCES_VERSION_KEY) !== LAYOUT_PREFERENCES_VERSION) {
      // v10.14.3 could persist a visually broken compact shell. Reset once so
      // every upgraded user starts from the readable, recoverable layout.
      storage.setItem(SIDEBAR_COMPACT_KEY, 'false')
      storage.setItem(UI_DENSITY_KEY, 'comfortable')
      storage.setItem(LAYOUT_PREFERENCES_VERSION_KEY, LAYOUT_PREFERENCES_VERSION)
    }
    return {
      sidebarCompact: storage.getItem(SIDEBAR_COMPACT_KEY) === 'true',
      density: storage.getItem(UI_DENSITY_KEY) === 'compact' ? 'compact' : 'comfortable',
    }
  } catch (_) {
    return { sidebarCompact: false, density: 'comfortable' }
  }
}

export default function App() {
  const shortRequestRef = useRef(false)
  const sourceRequestRef = useRef(false)
  const [sourceRequest, setSourceRequest] = useState('')
  const videoRef = useRef(null)
  const reviewVideoRef = useRef(null)
  const clipPreviewErrorRetries = useRef(0)
  const optimisticJobRef = useRef(null)
  const lastTerminalHydrationRef = useRef('')
  const lastGlobalTerminalSyncRef = useRef('')
  const youtubeCredentialsInputRef = useRef(null)
  const sourcePathInputRef = useRef(null)
  const utilityButtonRef = useRef(null)
  const utilityMenuRef = useRef(null)
  const projectScopeRef = useRef({ id: '', generation: 0 })
  const reviewRequestGuardRef = useRef(null)
  if (!reviewRequestGuardRef.current) reviewRequestGuardRef.current = createReviewRequestGuard()

  const [project, setProject] = useState(null)
  const [projects, setProjects] = useState([])
  const [status, setStatus] = useState({})
  const [systemCheck, setSystemCheck] = useState(null)
  const [systemCheckLoading, setSystemCheckLoading] = useState(false)
  const [progressExpanded, setProgressExpanded] = useState(false)
  const [logs, setLogs] = useState('')
  const [statusHistory, setStatusHistory] = useState([])
  const [candidates, setCandidates] = useState([])
  const [segments, setSegments] = useState([])
  const [segmentsRevision, setSegmentsRevision] = useState('')
  const [pendingCandidateKeys, setPendingCandidateKeys] = useState([])
  const [, setQuality] = useState(null)
  const [factory, setFactory] = useState(null)
  const [, setMetadata] = useState(null)
  const [, setBenchmark] = useState([])
  const [, setPreferences] = useState({ feedback: [] })
  const [, setResultCheck] = useState(null)
  const [outputs, setOutputs] = useState([])
  const [, setPreviewInfo] = useState({ exists: false })
  const [preRenderReport, setPreRenderReport] = useState(null)
  const [, setSimpleLog] = useState(null)
  const [visualQuality, setVisualQuality] = useState(null)
  const [, setQualityCore] = useState(null)
  const [, setAdaptiveRec] = useState(null)
  const [, setAiCoverage] = useState(null)
  const [, setRecovery] = useState(null)
  const [, setOllamaMonitor] = useState(null)
  const [autoRec, setAutoRec] = useState(null)
  const [smartPreflight, setSmartPreflight] = useState(null)
  const [checkpoints, setCheckpoints] = useState(null)
  const [, setCacheReport] = useState(null)
  const [durationControl, setDurationControl] = useState(null)
  const [renderQuality, setRenderQuality] = useState(null)
  const [creatorPack, setCreatorPack] = useState(null)
  const [projectHistory, setProjectHistory] = useState([])
  const [integrity, setIntegrity] = useState(null)
  const [, setProductReadiness] = useState(null)
  const [aiQualityAudit, setAiQualityAudit] = useState(null)
  const [, setProjectDoctor] = useState(null)
  const [, setProductTestPlan] = useState(null)
  const [workflowGuard, setWorkflowGuard] = useState(null)
  const [renderArtifactCheck, setRenderArtifactCheck] = useState(null)
  const [appAudit, setAppAudit] = useState(null)
  const [dashboardPollAfterMs, setDashboardPollAfterMs] = useState(12000)
  const [qualityLock, setQualityLock] = useState(null)
  const [finalSuccess, setFinalSuccess] = useState(null)
  const [successHistory, setSuccessHistory] = useState([])
  const [stableCandidate, setStableCandidate] = useState(null)
  const [bestProfile, setBestProfile] = useState(null)
  const [reviewFilter, setReviewFilter] = useState('all')
  const [notice, setNoticeState] = useState(null)
  const [clipPreview, setClipPreview] = useState({ url: '', loading: false, error: '', key: '' })
  const [clipPreviewReload, setClipPreviewReload] = useState(0)
  const [backendOk, setBackendOk] = useState(null)
  const [appBooting, setAppBooting] = useState(true)
  const [selectedFile, setSelectedFile] = useState(null)
  const [fastImportPath, setFastImportPath] = useState('')
  const [fastImportMode, setFastImportMode] = useState('reference')
  const [localBrowserOpen, setLocalBrowserOpen] = useState(false)
  const [localBrowser, setLocalBrowser] = useState({ roots: [], path: '', parent: '', dirs: [], videos: [] })
  const [localBrowseError, setLocalBrowseError] = useState('')
  const [twitchUrl, setTwitchUrl] = useState('')
  const [twitchKind, setTwitchKind] = useState('vod')
  const [twitchStart, setTwitchStart] = useState('')
  const [twitchEnd, setTwitchEnd] = useState('')
  const [twitchLiveMinutes, setTwitchLiveMinutes] = useState(60)
  const [twitchThreads, setTwitchThreads] = useState(16)
  const [twitchCookiesBrowser, setTwitchCookiesBrowser] = useState('none')
  const [twitchFormat] = useState('best')
  const [twitchEngine, setTwitchEngine] = useState('auto')
  const [twitchQuality, setTwitchQuality] = useState('best')
  const [twitchAria2Connections, setTwitchAria2Connections] = useState(16)
  const [twitchFallbackEnabled, setTwitchFallbackEnabled] = useState(true)
  const [, setTwitchTools] = useState(null)
  const [, setTwitchSpeedTest] = useState(null)
  const [twitchSpeedTesting, setTwitchSpeedTesting] = useState(false)
  const [sourceChoice, setSourceChoice] = useState('local')
  const [activeTab, setActiveTab] = useState('candidates')
  const [reviewTabTouched, setReviewTabTouched] = useState(false)
  const [activeStep, setActiveStep] = useState(() => localStorage.getItem('highlightStudioLastStep') || 'projects')
  const [formatConfirmed, setFormatConfirmed] = useState(false)
  const [taskCenterOpen, setTaskCenterOpen] = useState(false)
  useEffect(() => {
    if (status.state === 'error') setTaskCenterOpen(true)
  }, [project?.id, status.state])
  useEffect(() => {
    if (!taskCenterOpen) return
    const close = event => { if (event.key === 'Escape') setTaskCenterOpen(false) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [taskCenterOpen])
  const globalJobsSequence = useRef(0)
  const [globalJobs, setGlobalJobs] = useState([])
  const [globalJobsError, setGlobalJobsError] = useState('')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [sidebarCompact, setSidebarCompact] = useState(() => ensureLayoutPreferences().sidebarCompact)
  const [exportView, setExportView] = useState('video')
  const [advancedToolsOpen, setAdvancedToolsOpen] = useState(null)
  const [previewClip, setPreviewClip] = useState(null)
  const [search, setSearch] = useState('')
  const [, setSelectedTimelineId] = useState(null)
  const [expandedPreviewKey, setExpandedPreviewKey] = useState(null)
  const [productMode, setProductMode] = useState(() => localStorage.getItem('highlightStudioMode') || 'stable')
  const [uiDensity, setUiDensity] = useState(() => ensureLayoutPreferences().density)
  const [uiTheme, setUiTheme] = useState(() => normalizeTheme(localStorage.getItem('highlightStudioTheme') || DEFAULT_THEME))
  const [settingsTab, setSettingsTab] = useState('main')
  const [settingsQuery, setSettingsQuery] = useState('')
  const [reviewVisibleCount, setReviewVisibleCount] = useState(60)
  const [rejectedCandidateKeys, setRejectedCandidateKeys] = useState([])
  const [rejectedLoadedProjectId, setRejectedLoadedProjectId] = useState(null)
  const [trimDraft, setTrimDraft] = useState({ start: '', end: '' })
  const [segmentUndoStack, setSegmentUndoStack] = useState([])
  const [segmentRedoStack, setSegmentRedoStack] = useState([])
  const [showUtilityMenu, setShowUtilityMenu] = useState(false)
  const [diagnosticsExpanded, setDiagnosticsExpanded] = useState(false)
  const [desktopInfo, setDesktopInfo] = useState(null)
  const [onboarding, setOnboarding] = useState(null)
  const [firstRunOpen, setFirstRunOpen] = useState(false)
  const [updateState, setUpdateState] = useState({ supported: false, status: 'disabled', message: '' })
  const [supportBundleBusy, setSupportBundleBusy] = useState(false)
  const [licenseStatus, setLicenseStatus] = useState(null)
  const [authStatus, setAuthStatus] = useState(null)
  const [currentUser, setCurrentUser] = useState(null)
  const [projectAccess, setProjectAccess] = useState(null)
  const [youtubeStatus, setYoutubeStatus] = useState({ dependencies_available: true, credentials_configured: false, connected: false })
  const [youtubeReport, setYoutubeReport] = useState(null)
  const [youtubeBusy, setYoutubeBusy] = useState(false)
  const [youtubeForm, setYoutubeForm] = useState({
    file_path: '', title: '', description: '', tags: '', privacy_status: 'private',
    category_id: '22', notify_subscribers: false, made_for_kids: false, contains_synthetic_media: false, publish_at: '',
  })

  const setError = value => setNoticeState(previous => {
    const previousMessage = previous?.message || ''
    const next = typeof value === 'function' ? value(previousMessage) : value
    return normalizeNotice(next)
  })
  const setNotice = value => setNoticeState(normalizeNotice(value))

  const [settings, setSettings] = useState({
    ai_engine: 'ollama',
    ollama_url: 'http://localhost:11434',
    ollama_timeout: 900,
    ollama_keep_alive: '5m',
    ollama_num_ctx: 4096,
    hardware_profile: 'Auto',
    hardware_auto_optimize: true,
    hardware_quality_guard_enabled: true,
    cpu_worker_limit: 0,
    gpu_job_limit: 1,
    gpu_vram_reserve_mb: 768,
    hardware_decode: 'auto',
    analysis_profile: 'balanced',
    task_preset: 'balanced',
    task_preset_label: 'Сбалансированный / смысл',
    hybrid_disable_fallback: false,
    ai_batch_size: 3,
    ai_retry_count: 2,
    ai_ttft_timeout: 180,
    ai_stream_stall_timeout: 60,
    ai_circuit_failure_threshold: 2,
    ai_circuit_cooldown_seconds: 60,
    ai_strict_mode: false,
    full_ai_coverage: false,
    micro_batch_size: 8,
    text_model: 'qwen3:8b',
    vision_model: 'qwen3-vl:8b',
    whisper_model: 'small',
    whisper_device: 'auto',
    whisper_compute: 'auto',
    language: 'ru',
    block_seconds: 240,
    micro_cut_enabled: true,
    micro_window_seconds: 45,
    micro_min_seconds: 20,
    micro_max_seconds: 75,
    micro_speech_gap_seconds: 4,
    micro_source_duration_multiplier: 4,
    min_final_segments: 8,
    max_final_segments: 80,
    top_blocks_for_micro: 30,
    target_minutes: 30,
    visual_mode: 'Лёгкий',
    chunk_seconds: 900,
    strict_preflight: false,
    video_encoder: 'auto',
    render_preset: 'veryfast',
    crf: 23,
    batch_export_minutes: '10,30,60',
    shorts_count: 5,
    shorts_min_seconds: 3,
    shorts_max_seconds: 45,
    shorts_reframe_mode: 'auto',
    shorts_burn_subtitles: true,
    shorts_dynamic_captions: true,
    shorts_hook_title_enabled: true,
    shorts_trim_silence: true,
    shorts_caption_max_words: 4,
    shorts_caption_quality: 'high',
    shorts_caption_font_size: 72,
    shorts_hook_font_size: 82,
    shorts_caption_outline: 5,
    shorts_precise_alignment: true,
    shorts_whisper_model: 'small',
    shorts_recognition_dictionary: '',
    shorts_funny_search_enabled: true,
    shorts_emotion_events_enabled: true,
    shorts_llm_rerank_enabled: false,
    shorts_min_quality_score: 4.8,
    shorts_normalize_audio: true,
    shorts_render_preset: 'veryfast',
    shorts_crf: 22,
    dedup_enabled: true,
    storyline_enabled: true,
    make_srt: false,
    generate_metadata: false,
    metadata_ai_enabled: false,
    require_ai_metadata: false,
    metadata_timeout: 300,
    metadata_ai_retries: 5,
    metadata_max_segments: 12,
    remove_silence: false,
    audio_dynamics_enabled: true,
    refill_after_dedup_enabled: true,
    target_fill_ratio: 0.94,
    strict_quality_mode: false,
    strict_quality_min_score: 7.2,
    strict_quality_min_confidence: 6.2,
    semantic_quality_guard_enabled: true,
    non_primary_reject_confidence: 0.72,
    quality_first_selection_enabled: true,
    quality_first_min_score: 6.7,
    quality_first_min_confidence: 5.6,
    quality_first_min_clarity: 0.5,
    quality_recovery_score_relaxation: 0.5,
    temporal_fairness_enabled: true,
    temporal_fairness_bucket_seconds: 900,
    temporal_fairness_blocks_per_bucket: 2,
    temporal_fairness_min_score: 4.5,
    micro_global_score_floor: 7.6,
    auto_mode: 'Auto',
    content_type: 'Auto',
    auto_target_duration: true,
    preview_resolution: '720p',
    twitch_download_timeout: 43200,
    twitch_download_threads: 16,
    twitch_cookies_browser: 'none',
    twitch_format: 'best',
    twitch_download_engine: 'auto',
    twitch_quality: 'best',
    twitch_speed_test_seconds: 45,
    twitch_fallback_enabled: true,
    twitch_aria2_connections: 16,
    twitch_downloader_cli_path: '',
    edit_mode: 'Сбалансированный',
    visual_scan_enabled: true,
    visual_scan_interval_seconds: 5,
    visual_scan_max_samples: 1200,
    ocr_enabled: true,
    ocr_languages: 'rus+eng',
    ocr_every_n_visual_samples: 2,
    ocr_roi_enabled: false,
    ocr_roi_x: 0,
    ocr_roi_y: 0,
    ocr_roi_w: 1,
    ocr_roi_h: 1,
    ocr_upscale: 2,
    hook_first_enabled: false,
    hook_min_score: 8.7,
    shorts_vertical_reframe: true,
    prompt: `Собери плотную и интересную нарезку стрима для YouTube.

Главная цель: выбрать лучшие ЗАКОНЧЕННЫЕ моменты из ВСЕГО стрима, чтобы зрителю был понятен смысл даже без просмотра полного VOD.

Для каждой сцены сначала пойми: что стало причиной → что произошло/развилось → какая была реакция или payoff. При необходимости оставляй короткий контекст до и после события.

Оставляй: сильные реакции, неожиданные реплики, конфликты, споры, донаты и чат только когда они создают реальное событие, мемы, сильные эмоции, личные истории, ярких персонажей и сцены с развитием/развязкой.

Жёстко убирай: waiting/reconnect/intermission, экраны «пропал интернет/подключаюсь», технические паузы, рекламу, повторы, заранее записанные старые хайлайты, бессмысленные обрывки, реакцию без причины и разговор без payoff. Если стример СЕЙЧАС вживую реагирует на чужое видео, оценивай его текущую реакцию и комментарий, а не качество встроенного ролика.

Ранний timestamp не даёт преимуществ: момент в конце VOD должен иметь такой же шанс победить, как момент в начале.

Score 9-10 — выдающийся законченный момент.
Score 7-8 — сильный самостоятельный момент.
Score 5-6 — средний; НЕ использовать только ради добора целевой длительности.
Score 0-4 — вода, паузы, техника, replay/prerecorded или слабая сцена.

30 минут — это цель/верхняя граница, а не обязанность. Лучше короче, но действительно сильная и связная нарезка.`
  })

  const finalReady = Boolean(
    project?.freshness?.render_current ??
    outputs?.some?.(o => (o.path === 'outputs/highlight_final.mp4' || o.path === 'highlight_final.mp4') && o.current !== false)
  )
  const sourceReady = Boolean(
    project?.source_ready ?? project?.source_readiness?.ready ?? project?.source_readiness?.ok ?? project?.freshness?.source_ready ?? false
  )
  // Analysis completion is a persisted backend revision, not a proxy for how
  // many candidates happen to be loaded in React. A valid analysis may return
  // zero candidates, and the lightweight progress endpoint can reach `done` a
  // moment before the full candidate list is hydrated.
  const analysisCurrent = Boolean(
    project?.freshness?.analysis_current ?? project?.freshness?.candidates_current ?? false
  )
  const busy = ['running', 'queued', 'cancel_requested'].includes(String(status.state || '').toLowerCase())
  const progressValue = Math.max(0, Math.min(100, Number(status.progress) || 0))
  // Publisher only receives outputs that backend revision checks consider current.
  const youtubeVideoOutputs = useMemo(() => (outputs || []).filter(o => (o.kind === 'video' || o.kind === 'short') && o.current !== false), [outputs])
  const youtubeShortOutputs = useMemo(() => youtubeVideoOutputs.filter(o => o.kind === 'short'), [youtubeVideoOutputs])

  const timelineMax = useMemo(() => {
    return Math.max(1, ...segments.map(s => Number(s.end) || 0))
  }, [segments])

  const totalFinalDuration = useMemo(() => {
    return segments.reduce((sum, s) => sum + Math.max(0, (Number(s.end) || 0) - (Number(s.start) || 0)), 0)
  }, [segments])

  const filteredCandidates = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filter = (c) => {
      const blob = `${c.title || ''} ${c.reason || ''} ${c.text_preview || ''} ${c.ai_explanation || ''} ${c.what_happens || ''} ${c.why_selected || ''} ${c.viewer_value || ''} ${c.moment_type || ''}`.toLowerCase()
      if (q && !blob.includes(q)) return false
      if (reviewFilter === 'all') return true
      if (reviewFilter === 'weak') return Number(c.score || 0) < 6.5
      if (reviewFilter === 'repeat') return /повтор|duplicate|дубл/.test(blob)
      if (reviewFilter === 'silence') return /тишин|silence|пауза/.test(blob)
      const map = { funny: /смеш|смех|угар|funny|meme|мем/, conflict: /конфликт|спор|хаос|крик|conflict/, reaction: /реакц|шок|эмоц|reaction/, donation: /донат|чат|donation|chat/, sport: /спорт|теннис|розыгрыш|sport|tennis/, emotion: /эмоц|смех|шок|радост|злост|emotion/, dialog: /диалог|разговор|истор|dialog|story/ }
      return map[reviewFilter]?.test(blob) ?? true
    }
    return candidates.filter(c => !rejectedCandidateKeys.includes(candidateRejectKey(c))).filter(filter)
  }, [candidates, search, reviewFilter, rejectedCandidateKeys])

  useEffect(() => {
    const root = document.documentElement
    root.dataset.hsTheme = uiTheme
    root.style.colorScheme = 'dark'
    localStorage.setItem('highlightStudioTheme', uiTheme)
    return () => {
      if (root.dataset.hsTheme === uiTheme) delete root.dataset.hsTheme
    }
  }, [uiTheme])

  useEffect(() => {
    const saved = loadSafeLocalSettings()
    if (Object.keys(saved).length) setSettings(st => ({ ...st, ...saved }))
    readDesktopInfo().then(info => { if (info?.ok) setDesktopInfo(info) }).catch(() => {})
    getNativeUpdateStatus().then(info => { if (info?.ok) setUpdateState(info) }).catch(() => {})
    const unsubscribeUpdates = subscribeNativeUpdateStatus(payload => setUpdateState(payload || {}))
    Promise.resolve(initApp()).finally(() => setAppBooting(false))
    return () => unsubscribeUpdates()
    // Startup must run once; initApp is intentionally not a reactive dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!project?.id) {
      setFormatConfirmed(false)
      return
    }
    localStorage.setItem('highlightStudioLastProject', project.id)
    setFormatConfirmed(
      localStorage.getItem(`highlightStudioFormatConfirmed:${project.id}`) === 'true'
      || candidates.length > 0
      || segments.length > 0
    )
  // Candidate and segment completion are promoted in the focused effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id])

  useEffect(() => {
    if (!project?.id || (!candidates.length && !segments.length)) return
    setFormatConfirmed(true)
    localStorage.setItem(`highlightStudioFormatConfirmed:${project.id}`, 'true')
  }, [project?.id, candidates.length, segments.length])

  useEffect(() => {
    if (['import', 'style', 'analysis', 'review', 'export'].includes(activeStep)) {
      localStorage.setItem('highlightStudioLastStep', activeStep)
    }
  }, [activeStep])

  useEffect(() => {
    const id = String(project?.id || '')
    reviewRequestGuardRef.current.select(id)
    if (projectScopeRef.current.id !== id) {
      projectScopeRef.current = { id, generation: projectScopeRef.current.generation + 1 }
    }
  }, [project?.id])

  function beginProjectScope(projectId) {
    const id = String(projectId || '')
    reviewRequestGuardRef.current.select(id)
    const scope = { id, generation: projectScopeRef.current.generation + 1 }
    projectScopeRef.current = scope
    if (id) localStorage.setItem('highlightStudioLastProject', id)
    return scope
  }

  function captureProjectScope(projectId = project?.id) {
    return { id: String(projectId || ''), generation: projectScopeRef.current.generation }
  }

  function isProjectScopeCurrent(scope) {
    return Boolean(scope && projectScopeRef.current.id === scope.id && projectScopeRef.current.generation === scope.generation)
  }

  function resolveWorkflowAccessStep(stepId) {
    if (!project) {
      // Import is the only workflow step that must be reachable before a project
      // exists. Blocking it here made the "New project" button immediately
      // bounce back to the Projects screen before the file picker could render.
      return ['projects', 'import', 'reports', 'settings'].includes(stepId) ? stepId : 'projects'
    }
    if (stepId === 'style') {
      if (!sourceReady) return 'import'
      return 'style'
    }
    if (stepId === 'analysis') {
      if (!sourceReady) return 'import'
      if (!formatConfirmed) return 'style'
      return 'analysis'
    }
    if (stepId === 'review') {
      if (!sourceReady) return 'import'
      if (!formatConfirmed) return 'style'
      if (!analysisCurrent) return 'analysis'
      return 'review'
    }
    if (stepId === 'export') {
      if (!sourceReady) return 'import'
      if (!formatConfirmed) return 'style'
      if (!candidates.length) return 'analysis'
      if (!segments.length) return 'review'
      return 'export'
    }
    return stepId
  }

  useEffect(() => {
    // Do not validate a saved workflow step until startup has finished loading
    // the last project. Running this guard while project === null used to bounce
    // a restored Format/Analysis/Review/Export screen back to Projects.
    if (appBooting) return
    const nextStep = resolveWorkflowAccessStep(activeStep)
    if (nextStep === activeStep) return
    setActiveStep(nextStep)
    if (project?.source_type === 'twitch' && nextStep === 'import' && !sourceReady && busy) {
      setNotice({
        type: 'info',
        title: 'VOD ещё подготавливается',
        message: 'Пока Twitch VOD скачивается, интерфейс остаётся на этапе «Источник». После подготовки видео можно перейти к формату.',
      })
    }
  }, [appBooting, activeStep, project?.id, project?.source_type, sourceReady, formatConfirmed, analysisCurrent, candidates.length, segments.length, busy])

  useEffect(() => {
    if (!busy) return
    // Keep long-running jobs from moving the whole workspace. Progress is always
    // visible in the top Tasks button and on the active workflow page; the
    // detailed Task Center opens only when the user asks for it.
    localStorage.setItem('highlightStudioActiveTask', JSON.stringify({
      projectId: project?.id || '',
      projectName: project?.name || '',
      state: status.state,
      stage: status.stage || '',
      message: status.message || '',
      progress: progressValue,
      savedAt: Date.now(),
    }))
  }, [busy, project?.id, project?.name, progressValue, status.message, status.stage, status.state])

  useEffect(() => {
    if (!showUtilityMenu) return
    const menu = utilityMenuRef.current
    menu?.querySelector('button')?.focus()
    const closeMenu = event => {
      if (event.key === 'Escape') {
        setShowUtilityMenu(false)
        utilityButtonRef.current?.focus()
        return
      }
      if (event.type === 'mousedown' && !menu?.contains(event.target) && !utilityButtonRef.current?.contains(event.target)) {
        setShowUtilityMenu(false)
      }
    }
    document.addEventListener('keydown', closeMenu)
    document.addEventListener('mousedown', closeMenu)
    return () => {
      document.removeEventListener('keydown', closeMenu)
      document.removeEventListener('mousedown', closeMenu)
    }
  }, [showUtilityMenu])
  useEffect(() => {
    if (!project?.id) return
    let cancelled = false
    let timer = null
    const busyState = ['running', 'queued', 'cancel_requested'].includes(String(status?.state || '').toLowerCase())
    const delay = Math.max(2000, Number(dashboardPollAfterMs || (busyState || activeStep === 'analysis' || activeStep === 'export' ? 2500 : 12000)))
    const poll = async () => {
      if (cancelled) return
      const data = busyState ? await refreshProgress(project.id) : await refreshAll(project.id)
      // A terminal lightweight response is immediately followed by one full
      // refresh so candidates/reports/outputs become current exactly once.
      if (!cancelled && busyState && data?.terminal) await refreshAll(project.id)
      if (!cancelled) timer = setTimeout(poll, delay)
    }
    timer = setTimeout(poll, delay)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // refreshAll is intentionally read from the latest render while the timer is recreated by state dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, status?.state, activeStep, dashboardPollAfterMs])

  useEffect(() => {
    // Belt-and-suspenders terminal hydration.  The active-job poll already asks
    // for one full dashboard snapshot after a terminal progress response, but a
    // render/timer race on slow Windows machines must never leave the UI with
    // `done` status and the pre-analysis empty candidates array.
    const state = String(status?.state || '').toLowerCase()
    if (!project?.id || !['done', 'error', 'cancelled'].includes(state)) return
    const key = `${project.id}:${state}:${status?.updated_at || status?.finished_at || status?.message || ''}`
    if (lastTerminalHydrationRef.current === key) return
    lastTerminalHydrationRef.current = key
    refreshAll(project.id)
    // refreshAll is intentionally not a dependency: the terminal key makes this
    // effect idempotent and project generation guards protect against stale data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, status?.state, status?.updated_at, status?.finished_at, status?.message])


  useEffect(() => {
    setReviewVisibleCount(60)
  }, [search, reviewFilter, project?.id])
  useEffect(() => {
    if (!project?.id) { setProjectAccess(null); return }
    if (!authStatus?.accounts_enabled) { setProjectAccess({ role: 'owner', can_view: true, can_edit: true, can_manage: true }); return }
    const scope = captureProjectScope(project.id)
    const controller = new AbortController()
    apiFetch(`${API}/projects/${project.id}/access`, { signal: controller.signal }).then(async response => {
      if (!response.ok || !isProjectScopeCurrent(scope)) return
      const access = await safeJsonResponse(response, null)
      if (isProjectScopeCurrent(scope)) setProjectAccess(access)
    }).catch(error => {
      if (error?.name !== 'AbortError' && isProjectScopeCurrent(scope)) setProjectAccess(null)
    })
    return () => controller.abort()
    // scope helpers use refs and intentionally stay outside dependency list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, authStatus?.accounts_enabled])

  useEffect(() => {
    setYoutubeForm({
      file_path: '', title: '', description: '', tags: '', privacy_status: 'private',
      category_id: '22', notify_subscribers: false, made_for_kids: false, contains_synthetic_media: false, publish_at: '',
    })
    setYoutubeReport(null)
    refreshYoutubeStatus(false)
    // Reset and refresh only when the selected project changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id])

  useEffect(() => {
    if (!youtubeVideoOutputs.length) return
    setYoutubeForm(prev => {
      if (youtubeVideoOutputs.some(item => item.path === prev.file_path)) return prev
      const preferred = youtubeVideoOutputs.find(item => item.path === 'outputs/highlight_final.mp4' || item.path === 'highlight_final.mp4') || youtubeVideoOutputs[0]
      return { ...prev, file_path: preferred?.path || '' }
    })
  }, [youtubeVideoOutputs])


  useEffect(() => {
    if (!project?.id) {
      setRejectedCandidateKeys([])
      setRejectedLoadedProjectId(null)
      return
    }
    let loaded = []
    try {
      const parsed = JSON.parse(localStorage.getItem(`highlightRejected:${project.id}`) || '[]')
      loaded = Array.isArray(parsed) ? parsed : []
    } catch (_) {
      loaded = []
    }
    setRejectedCandidateKeys(loaded)
    setRejectedLoadedProjectId(project.id)
  }, [project?.id])

  useEffect(() => {
    if (!project?.id || rejectedLoadedProjectId !== project.id) return
    localStorage.setItem(`highlightRejected:${project.id}`, JSON.stringify(rejectedCandidateKeys.slice(-1000)))
  }, [project?.id, rejectedLoadedProjectId, rejectedCandidateKeys])

  useEffect(() => {
    // Selection and local edit history belong to one project only. Keeping them
    // while switching projects could preview or undo clips from the previous one.
    setPreviewClip(null)
    setSelectedTimelineId(null)
    setExpandedPreviewKey(null)
    setTrimDraft({ start: '', end: '' })
    setSegmentUndoStack([])
    setSegmentRedoStack([])
    setPendingCandidateKeys([])
    setActiveTab('candidates')
    setReviewTabTouched(false)
  }, [project?.id])

  useEffect(() => {
    if (activeStep !== 'review' || reviewTabTouched) return
    setActiveTab(segments.length > 0 ? 'final' : 'candidates')
  }, [activeStep, project?.id, segments.length, reviewTabTouched])

  useEffect(() => {
    if (!notice || notice.persistent || notice.type === 'error') return
    const timer = setTimeout(() => setNoticeState(null), 5200)
    return () => clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    if (settings.twitch_download_engine) setTwitchEngine(settings.twitch_download_engine)
    if (settings.twitch_quality) setTwitchQuality(settings.twitch_quality)
    if (settings.twitch_aria2_connections) setTwitchAria2Connections(settings.twitch_aria2_connections)
    if (settings.twitch_fallback_enabled !== undefined) setTwitchFallbackEnabled(Boolean(settings.twitch_fallback_enabled))
  }, [settings.twitch_download_engine, settings.twitch_quality, settings.twitch_aria2_connections, settings.twitch_fallback_enabled])

  useEffect(() => {
    if (!previewClip || !videoRef.current) return
    const v = videoRef.current
    const start = Number(previewClip.start) || 0
    const end = Number(previewClip.end) || 0
    v.currentTime = start
    const onTime = () => {
      if (end && v.currentTime >= end) v.pause()
    }
    v.addEventListener('timeupdate', onTime)
    v.play().catch(() => {})
    return () => v.removeEventListener('timeupdate', onTime)
  }, [previewClip])


  async function initApp() {
    let initialAuth = null
    try {
      const health = await fetch(`${API}/health`, { credentials: 'include' })
      await safeJsonResponse(health, null)
      const authResponse = await apiFetch(`${API}/auth/status`)
      initialAuth = await safeJsonResponse(authResponse, null)
      if (initialAuth) { setAuthStatus(initialAuth); setCurrentUser(initialAuth.user || null) }
      if (initialAuth?.accounts_enabled && !initialAuth?.authenticated) return
    } catch (_) {}
    // First fetch /api/health to store the local token, then run the readiness check.
    // In v9.3.0 these calls started in parallel; /api/system-check could return 401
    // before the token was saved, so the UI showed every dependency as broken.
    const availableProjects = await refreshProjects()
    await refreshTwitchTools()
    const webNonAdmin = initialAuth?.accounts_enabled && initialAuth?.user?.global_role !== 'admin'
    if (webNonAdmin) return null
    const lastProjectId = localStorage.getItem('highlightStudioLastProject')
    if (lastProjectId && availableProjects.some(item => item.id === lastProjectId)) {
      await loadProjectById(lastProjectId, { quiet: true })
    } else {
      setActiveStep('projects')
    }
    const readiness = await runSystemCheck(false)
    try {
      const [onboardingResponse, migrationResponse, recoveryResponse, licenseResponse] = await Promise.all([
        apiFetch(`${API}/onboarding`),
        apiFetch(`${API}/migrations/status`),
        apiFetch(`${API}/startup-recovery`),
        apiFetch(`${API}/license/status`),
      ])
      const payload = await safeJsonResponse(onboardingResponse, null)
      const migrations = await safeJsonResponse(migrationResponse, null)
      const recovery = await safeJsonResponse(recoveryResponse, null)
      const currentLicense = await safeJsonResponse(licenseResponse, null)
      if (licenseResponse.ok && currentLicense) setLicenseStatus(currentLicense)
      if (onboardingResponse.ok && payload) {
        setOnboarding(payload)
        setFirstRunOpen(!payload.completed)
      }
      if (migrationResponse.ok && migrations?.failed_count) {
        setError(`Не удалось обновить ${migrations.failed_count} проект(а). Создай отчёт для поддержки перед продолжением.`)
      } else if (recoveryResponse.ok && recovery?.recovered_count) {
        setError(`После предыдущего закрытия восстановлено проектов: ${recovery.recovered_count}. Можно продолжить с контрольной точки.`)
      }
    } catch (_) {}
    return readiness
  }

  async function runSystemCheck(showNotice = true) {
    setSystemCheckLoading(true)
    try {
      const r = await apiFetch(`${API}/system-check`)
      if (!r.ok) throw new Error(`system-check ${r.status}: ${(await r.text()).slice(0, 300)}`)
      const data = await r.json()
      setSystemCheck(data)
      if (showNotice) setError(data.ok ? 'Проверка системы пройдена: можно запускать анализ.' : `Проверка системы: ${data.summary}. ${data.recommendations?.[0] || ''}`)
      return data
    } catch (e) {
      setError(`Проверка системы не сработала: ${e.message}`)
      return null
    } finally {
      setSystemCheckLoading(false)
    }
  }


  async function refreshTwitchTools() {
    try {
      const r = await apiFetch(`${API}/twitch/tools`)
      const data = await safeJsonResponse(r, null)
      if (r.ok && data && !data.non_json_response) setTwitchTools(data)
    } catch (_) {}
  }




  async function loadProjectById(id, { quiet = false } = {}) {
    if (!id) return
    const scope = beginProjectScope(id)
    try {
      const r = await apiFetch(`${API}/projects/${id}`)
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setProject(data)
      setSettings(st => ({ ...st, ...(data.settings || {}) }))
      if (authStatus?.accounts_enabled) {
        const accessResponse = await apiFetch(`${API}/projects/${id}/access`)
        if (accessResponse.ok) {
          const access = await safeJsonResponse(accessResponse, null)
          if (isProjectScopeCurrent(scope)) setProjectAccess(access)
        }
      } else if (isProjectScopeCurrent(scope)) {
        setProjectAccess({ role: 'owner', can_view: true, can_edit: true, can_manage: true })
      }
      const dashboard = await refreshAll(id, scope)
      if (!isProjectScopeCurrent(scope)) return null
      if (!quiet) {
        const fresh = dashboard?.freshness || dashboard?.project?.freshness || data?.freshness || {}
        const sourceIsReady = Boolean(dashboard?.source_readiness?.ready ?? dashboard?.source_readiness?.ok ?? data?.source_ready ?? data?.source_readiness?.ready)
        const candidateCount = Array.isArray(dashboard?.candidates) ? dashboard.candidates.length : 0
        const segmentCount = Array.isArray(dashboard?.segments) ? dashboard.segments.length : 0
        const analysisIsCurrent = Boolean(fresh.analysis_current ?? fresh.candidates_current ?? candidateCount > 0)
        const formatIsConfirmed = localStorage.getItem(`highlightStudioFormatConfirmed:${id}`) === 'true' || analysisIsCurrent || candidateCount > 0 || segmentCount > 0
        const preferred = localStorage.getItem('highlightStudioLastStep') || 'analysis'
        let resumeStep = preferred
        if (!sourceIsReady) resumeStep = 'import'
        else if (!formatIsConfirmed && ['analysis', 'review', 'export'].includes(resumeStep)) resumeStep = 'style'
        else if (!analysisIsCurrent && ['review', 'export'].includes(resumeStep)) resumeStep = 'analysis'
        else if (!segmentCount && resumeStep === 'export') resumeStep = analysisIsCurrent ? 'review' : 'analysis'
        if (!['import', 'style', 'analysis', 'review', 'export'].includes(resumeStep)) resumeStep = sourceIsReady ? (formatIsConfirmed ? 'analysis' : 'style') : 'import'
        setActiveStep(resumeStep)
        setNotice({ type: 'success', title: 'Проект открыт', message: `${data.name || id}. Этапы больше не переключаются автоматически — переходи по меню, когда удобно.` })
      }
      return data
    } catch (e) {
      if (isProjectScopeCurrent(scope)) notifyError('Не удалось открыть проект', e)
      return null
    }
  }

  async function refreshProjects() {
    try {
      const h = await apiFetch(`${API}/health`)
      if (!h.ok) throw new Error('Backend health failed')
      await h.json()
      setBackendOk(true)
      const r = await apiFetch(`${API}/projects`)
      if (!r.ok) throw new Error(`Projects error ${r.status}`)
      const items = await r.json()
      setProjects(items)
      // Do not clear user-facing notices here: periodic refresh can erase 'settings saved' messages.
      return items
    } catch (e) {
      setBackendOk(false)
      setNotice({
        type: 'error',
        title: 'Локальный движок недоступен',
        message: `${e.message}. Перезапусти приложение или повтори проверку.`,
        persistent: true,
        action: { label: 'Повторить', id: 'retry-backend' },
      })
      return []
    }
  }

  async function refreshGlobalJobs(signal) {
    const sequence = ++globalJobsSequence.current
    try {
      const response = await apiFetch(`${API}/jobs`, { signal })
      if (!response.ok) throw new Error(`jobs ${response.status}`)
      const items = await safeJsonResponse(response, [])
      const normalized = Array.isArray(items) ? items : []
      if (signal?.aborted || sequence !== globalJobsSequence.current) return []
      setGlobalJobs(normalized)
      setGlobalJobsError('')
      return normalized
    } catch (error) {
      if (signal?.aborted || sequence !== globalJobsSequence.current) return []
      setGlobalJobsError(`Не удалось обновить фоновые задачи: ${humanizeErrorMessage(error?.message || error)}`)
      return []
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    const stop = startSerialPoll(() => refreshGlobalJobs(controller.signal), taskCenterOpen ? 4000 : 8000)
    return () => { controller.abort(); stop() }
    // refreshGlobalJobs uses setters and a persistent sequence ref, not UI state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskCenterOpen])

  useEffect(() => {
    // The global job list is an independent source of truth.  On 10.15.11 the
    // production bundle could show a completed One-click job in Task Center
    // while the active React project still held the pre-analysis dashboard
    // snapshot (0 candidates, Montage locked).  Whenever the current project's
    // analysis job reaches a terminal successful state, hydrate the complete
    // dashboard once.  Navigation remains manual: this only unlocks Review.
    if (!project?.id || !Array.isArray(globalJobs) || !globalJobs.length) return
    const terminal = globalJobs.find(job => {
      const sameProject = String(job?.project_id || '') === String(project.id)
      const state = String(job?.state || '').toLowerCase()
      const kind = String(job?.kind || '').toLowerCase()
      return sameProject && state === 'done' && ['one_click', 'analysis', 'analyze', 'ai_analyze'].includes(kind)
    })
    if (!terminal) return
    const key = `${project.id}:${terminal.id || terminal.job_id || terminal.kind}:${terminal.finished_at || terminal.updated_at || terminal.progress || 100}`
    if (lastGlobalTerminalSyncRef.current === key) return
    lastGlobalTerminalSyncRef.current = key
    const scope = captureProjectScope(project.id)
    refreshAll(project.id, scope).then(data => {
      if (!data || !isProjectScopeCurrent(scope)) return
      const fresh = data.freshness || data.project?.freshness || {}
      const candidateCount = Array.isArray(data.candidates) ? data.candidates.length : 0
      const segmentCount = Array.isArray(data.segments) ? data.segments.length : 0
      const current = Boolean(fresh.analysis_current ?? fresh.candidates_current ?? candidateCount > 0)
      if (!current) return
      setFormatConfirmed(true)
      localStorage.setItem(`highlightStudioFormatConfirmed:${project.id}`, 'true')
      setNotice({
        type: 'success',
        title: 'Анализ завершён',
        message: candidateCount
          ? `Найдено ${candidateCount} моментов${segmentCount ? ` · AI-нарезка: ${segmentCount}` : ''}. Монтаж разблокирован — открой его, когда будешь готов.`
          : 'Анализ завершён. Монтаж разблокирован для проверки результата и диагностики.',
      })
    }).catch(() => {})
    // refreshAll/captureProjectScope are intentionally read from the latest
    // render; the terminal key keeps this synchronization idempotent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [globalJobs, project?.id])

  function humanizePipelineStage(stage) {
    const value = String(stage || '').trim()
    const key = value.toLowerCase()
    const labels = {
      preparing: 'Подготавливаем видео', source: 'Подготавливаем видео',
      whisper: 'Распознаём речь', transcription: 'Распознаём речь',
      block_ai: 'Анализируем содержание стрима', micro_ai: 'Уточняем лучшие моменты',
      visual_scan: 'Проверяем изображение', ocr_scan: 'Читаем текст и чат',
      audio_analysis: 'Проверяем звук и реакции', render: 'Собираем итоговое видео',
      twitch_vod: 'Скачиваем Twitch VOD', twitch_import: 'Подготавливаем Twitch-видео', 'twitch-import': 'Подготавливаем Twitch-видео',
      shorts_single: 'Пересобираем Short', shorts: 'Создаём Shorts',
      render_shorts: 'Создаём Shorts', download: 'Скачиваем исходник', twitch_download: 'Скачиваем Twitch VOD',
    }
    return labels[key] || value.replaceAll('_', ' ') || 'Обработка'
  }

  function jobStateLabel(state) {
    const key = String(state || '').toLowerCase()
    return ({ queued: 'В очереди', running: 'Выполняется', done: 'Готово', error: 'Ошибка', cancelled: 'Остановлено', interrupted: 'Прервано', cancel_requested: 'Останавливается' })[key] || key || 'Сохранено'
  }

  function applyDashboardState(data) {
    if (!data || data.ok === false) return
    if (data.project) {
      setProject(prev => prev?.id === data.project.id ? ({ ...prev, ...data.project }) : data.project)
    }
    const incomingStatus = data.status || {}
    const incomingState = String(incomingStatus.state || '').toLowerCase()
    const optimistic = optimisticJobRef.current
    if (optimistic && Date.now() < optimistic.keepUntil && !['running', 'queued', 'done', 'error', 'cancelled'].includes(incomingState)) {
      // Keep the just-started Render/Shorts/Analyze bar visible if the first
      // dashboard refresh arrives before the worker has written its running
      // status. Without this, the UI could briefly revert to old/empty status
      // and the user would think the progress bar did not work.
      setStatus(prev => Object.keys(prev || {}).length ? prev : optimistic.status)
    } else {
      if (['running', 'queued', 'done', 'error', 'cancelled'].includes(incomingState)) optimisticJobRef.current = null
      setStatus(incomingStatus)
    }
    setStatusHistory(data.status_history || [])
    setLogs(data.logs || '')
    setCandidates(data.candidates || [])
    setSegments(data.segments || [])
    setSegmentsRevision(data.freshness?.segments_revision || data.project?.freshness?.segments_revision || '')
    setQuality(data.quality || null)
    const nextFactory = data.factory || null
    setFactory(nextFactory)
    setMetadata(data.metadata || null)
    setBenchmark(data.benchmark || [])
    setPreferences(data.preferences || { feedback: [] })
    setResultCheck(data.result_check || null)
    setOutputs(data.outputs || [])
    setYoutubeReport(data.youtube_upload_report || null)
    setPreviewInfo(data.preview_status || { exists: false })
    setPreRenderReport(data.pre_render_check || null)
    setSimpleLog(data.simple_log || null)
    setVisualQuality(data.visual_quality || null)
    setQualityCore(data.quality_core || null)
    setAdaptiveRec(data.adaptive_recommendation || null)
    setAiCoverage(data.ai_coverage || null)
    setRecovery(data.recovery || null)
    setOllamaMonitor(data.ollama_monitor || null)
    setAutoRec(data.auto_recommendation || null)
    setSmartPreflight(data.smart_preflight || null)
    setCheckpoints(data.checkpoints || null)
    setCacheReport(data.cache_fingerprint || null)
    setDurationControl(data.duration_control || null)
    setRenderQuality(data.quality_before_render || null)
    setCreatorPack(data.creator_pack || null)
    setProjectHistory(data.project_history?.items || [])
    setIntegrity(data.integrity || null)
    setProductReadiness(data.product_readiness || null)
    setAiQualityAudit(data.ai_quality_audit || null)
    setProjectDoctor(data.project_doctor || null)
    setQualityLock(data.quality_lock || null)
    setFinalSuccess(data.final_success || null)
    setStableCandidate(data.stable_candidate || null)
    setSuccessHistory(data.success_history?.items || [])
    setBestProfile(data.quality_lock?.exists ? data.quality_lock : null)
    setProductTestPlan(data.product_test_plan || null)
    setWorkflowGuard(data.workflow_guard || null)
    setRenderArtifactCheck(data.render_artifact_check || null)
    setAppAudit(data.app_audit || null)
    if (data.poll_after_ms) setDashboardPollAfterMs(data.poll_after_ms)
  }

  function applyProgressState(data) {
    if (!data || data.ok === false) return
    const incomingStatus = data.status || {}
    const incomingState = String(incomingStatus.state || '').toLowerCase()
    const optimistic = optimisticJobRef.current
    if (optimistic && Date.now() < optimistic.keepUntil && !['running', 'queued', 'done', 'error', 'cancelled'].includes(incomingState)) {
      setStatus(prev => Object.keys(prev || {}).length ? prev : optimistic.status)
    } else {
      if (['running', 'queued', 'done', 'error', 'cancelled'].includes(incomingState)) optimisticJobRef.current = null
      // Keep heavy diagnosis fields from the last full dashboard refresh while
      // replacing only the live job counters. This avoids re-allocating the
      // entire dashboard tree every 2.5 seconds during multi-hour analysis.
      setStatus(prev => ({ ...(prev || {}), ...incomingStatus }))
    }
    setStatusHistory(data.status_history || [])
    setLogs(data.logs || '')
    if (data.ollama_monitor != null) setOllamaMonitor(data.ollama_monitor)
    if (data.poll_after_ms) setDashboardPollAfterMs(data.poll_after_ms)
  }

  async function refreshProgress(id = project?.id, existingScope = null) {
    if (!id) return null
    const scope = existingScope || captureProjectScope(id)
    try {
      const r = await apiFetch(`${API}/projects/${id}/progress-state`)
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false || data?.non_json_response) {
        throw new Error(errorFromPayload(data, `progress-state ${r.status}`))
      }
      if (!isProjectScopeCurrent(scope)) return null
      applyProgressState(data)
      return data
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Не удалось обновить прогресс: ${e.message}`)
      return null
    }
  }

  async function refreshAll(id = project?.id, existingScope = null) {
    if (!id) return null
    const scope = existingScope || captureProjectScope(id)
    const readToken = reviewRequestGuardRef.current.read('dashboard')
    try {
      const r = await apiFetch(`${API}/projects/${id}/dashboard-state`)
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false || data?.non_json_response) {
        throw new Error(errorFromPayload(data, `dashboard-state ${r.status}`))
      }
      // Every project-scoped response is generation guarded. A late response
      // from A can never mutate state after the user has switched to B.
      if (!isProjectScopeCurrent(scope)) return null
      if (!reviewRequestGuardRef.current.accepts(readToken)) return null
      applyDashboardState(data)
      return data
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Не удалось обновить проект: ${e.message}`)
      return null
    }
  }


  async function applyCurrentSettingsToNewProject(p) {
    // A new project must not silently inherit hidden prompts, thresholds, cache
    // knobs or expert compute settings from the previous project. Carry only
    // explicit user-facing intent selected before source creation.
    const intentKeys = new Set([
      'content_type', 'edit_mode', 'target_minutes', 'task_preset',
      'analysis_profile', 'subtitles_enabled', 'make_srt', 'output_resolution',
      'hardware_auto_optimize', 'hardware_profile',
    ])
    const intent = Object.fromEntries(Object.entries(settings || {}).filter(([key]) => intentKeys.has(key)))
    const merged = { ...(p.settings || {}), ...intent }
    try {
      const saved = await persistSettingsForProject(p.id, merged)
      setSettings(st => ({ ...st, ...saved }))
      setProject(pr => pr?.id === p.id ? ({ ...pr, settings: { ...(pr.settings || {}), ...saved } }) : pr)
      return { ...p, settings: { ...(p.settings || {}), ...saved } }
    } catch (e) {
      setSettings(st => ({ ...st, ...(p.settings || {}) }))
      setError(`Проект создан, но настройки не применились: ${humanizeErrorMessage(e.message)}`)
      return p
    }
  }

  function activateNewProject(nextProject, nextStep = 'style') {
    beginProjectScope(nextProject.id)
    setProject(nextProject)
    optimisticJobRef.current = null
    setStatus({})
    setSegmentsRevision('')
    setFormatConfirmed(false)
    localStorage.removeItem(`highlightStudioFormatConfirmed:${nextProject.id}`)
    setActiveStep(nextStep)
  }

  function confirmFormat() {
    if (!project?.id) return
    setFormatConfirmed(true)
    localStorage.setItem(`highlightStudioFormatConfirmed:${project.id}`, 'true')
  }

  function continueToAnalysis() {
    if (!project?.id) {
      setError('Сначала создай проект.')
      return
    }
    if (!sourceReady) {
      setNotice({ type: 'warning', title: 'Источник ещё не готов', message: 'Дождись завершения подготовки VOD или локального видео.' })
      return
    }

    // Navigation must never wait for the heavyweight dashboard refresh.
    // Persist the format marker first, switch the workflow immediately, then
    // save the chosen preset in the background without refreshAll().
    confirmFormat()
    setActiveStep('analysis')
    setMobileNavOpen(false)
    saveSettings(null, { refresh: false, announce: false }).catch(exception => {
      notifyError('Настройки формата не сохранились', exception)
    })
  }

  async function uploadFile(file = selectedFile) {
    if (!file) return setError('Сначала выбери видеофайл')
    if (file.size > 2 * 1024 * 1024 * 1024) {
      setFastImportPath('')
      return setError('Файл больше 2 GB. Для больших стримов используй Fast Import → «Обзор диска» → «Без копирования». Так 12 GB не копируются в проект.')
    }
    if (sourceRequestRef.current) return
    sourceRequestRef.current = true
    setSourceRequest('Загружаем файл…')
    setTaskCenterOpen(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await apiFetch(`${API}/projects`, { method: 'POST', body: fd })
      if (!r.ok) throw new Error(`Upload error ${r.status}: ${(await r.text()).slice(0, 300)}`)
      const p = await r.json()
      activateNewProject(p)
      await applyCurrentSettingsToNewProject(p)
      await refreshProjects()
      await refreshAll(p.id)
    } catch (e) {
      setError(`Видео не загрузилось: ${e.message}`)
    } finally {
      sourceRequestRef.current = false
      setSourceRequest('')
    }
  }

  async function upload(e) {
    const file = e.target.files?.[0]
    setSelectedFile(file || null)
    if (file) await uploadFile(file)
  }

  async function responseError(r) {
    const payload = await safeJsonResponse(r, null)
    if (payload && !payload.non_json_response) return errorFromPayload(payload, `HTTP ${r.status}`)
    return (payload?.message || `HTTP ${r.status}`).slice(0, 800)
  }

  function humanizeErrorMessage(raw) {
    const msg = String(raw || '')
    const low = msg.toLowerCase()
    if (low.includes('winerror 10061') || low.includes('connection refused') || low.includes('localhost:11434') || low.includes('max retries')) {
      return 'Ollama не отвечает. Открой PowerShell и запусти: ollama serve. Потом нажми «Система» или «Проверка проекта».'
    }
    if (low.includes('yt-dlp') || low.includes('twitch vod')) {
      return 'Twitch VOD не подготовился. Проверь ссылку, интернет, доступность VOD и наличие yt-dlp. Для теста выбери диапазон 10 минут.'
    }
    if (low.includes('streamlink')) {
      return 'Twitch Live не записался. Проверь streamlink, ссылку канала и интернет.'
    }
    if (low.includes('tesseract')) {
      return 'Tesseract OCR недоступен. Анализ без OCR продолжит работать, но чат/донаты с экрана читаться не будут.'
    }
    if (low.includes('401') || low.includes('unauthorized')) {
      return 'Ошибка авторизации локального API. Перезагрузи страницу или перезапусти приложение через START_HERE.bat.'
    }
    if (low.includes('500 internal') || low.includes('traceback')) {
      return 'Внутренняя ошибка приложения. Открой вкладку «Отчёты → Лог» или файл startup.log, чтобы увидеть техническую причину.'
    }
    return msg
  }

  function notifyError(prefix, err) {
    const text = err?.message || err || ''
    setError(`${prefix}: ${humanizeErrorMessage(text)}`)
  }

  function setMode(mode) {
    setProductMode(mode)
    localStorage.setItem('highlightStudioMode', mode)
  }

  function toggleDensity() {
    const next = uiDensity === 'compact' ? 'comfortable' : 'compact'
    setUiDensity(next)
    persistLayoutPreference(UI_DENSITY_KEY, next)
  }

  function selectTheme(themeId) {
    const next = normalizeTheme(themeId)
    setUiTheme(next)
    localStorage.setItem('highlightStudioTheme', next)
  }




  async function applyHardwarePreset(kind = 'auto_balanced') {
    const modeLabel = kind.includes('fast') ? 'Быстрый автопрофиль' : kind.includes('quality') ? 'Автопрофиль качества' : 'Сбалансированный автопрофиль'
    if (!project) {
      setSettings(prev => ({
        ...prev,
        hardware_auto_optimize: true,
        hardware_profile: 'Auto',
        whisper_device: 'auto',
        whisper_compute: 'auto',
        video_encoder: 'auto',
        analysis_profile: kind.includes('fast') ? 'fast' : kind.includes('quality') ? 'quality' : 'balanced',
      }))
      setError(`${modeLabel} выбран. После создания проекта Highlight Studio сам проверит CPU, RAM, CUDA и NVENC.`)
      return
    }
    try {
      const projectId = project.id
      const scope = captureProjectScope(projectId)
      setError('Проверяю CPU/GPU и рассчитываю оптимальные настройки…')
      const r = await apiFetch(`${API}/projects/${projectId}/hardware-preset`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: kind })
      })
      if (!r.ok) throw new Error(await responseError(r))
      const data = await r.json()
      setProjects(items => items.map(item => item.id === projectId ? ({ ...item, settings: { ...(item.settings || {}), ...(data.settings || {}) } }) : item))
      if (!isProjectScopeCurrent(scope)) return
      setSettings(prev => ({ ...prev, ...(data.settings || {}) }))
      setProject(pj => pj?.id === projectId ? ({ ...pj, settings: { ...(pj.settings || {}), ...(data.settings || {}) }, hardware_capabilities: data.hardware || pj.hardware_capabilities }) : pj)
      setError(`${data.label || modeLabel} применён. ${data.message || ''}`)
      runSystemCheck(true)
    } catch (e) {
      setError(`Не удалось применить ${modeLabel.toLowerCase()}: ${e.message}`)
    }
  }

  async function browseLocal(path = '') {
    setLocalBrowseError('')
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : ''
      const r = await apiFetch(`${API}/local/browse${query}`)
      if (!r.ok) throw new Error(await responseError(r))
      const data = await r.json()
      setLocalBrowser(data)
      return data
    } catch (e) {
      setLocalBrowseError(e.message)
      return null
    }
  }

  async function openLocalBrowser() {
    try {
      const nativeResult = await chooseNativeVideo()
      if (nativeResult) {
        if (nativeResult.ok && nativeResult.path) {
          setFastImportPath(nativeResult.path)
          setLocalBrowserOpen(false)
          setError(`Выбран файл: ${nativeResult.name || nativeResult.path}`)
        } else if (!nativeResult.canceled && nativeResult.message) {
          setError(nativeResult.message)
        }
        return
      }
    } catch (e) {
      setError(`Системное окно выбора не открылось: ${e.message}. Использую встроенный обзор.`)
    }
    setLocalBrowserOpen(true)
    const typed = fastImportPath.trim()
    const loaded = typed ? await browseLocal(typed) : await browseLocal('')
    if (!loaded && typed) await browseLocal('')
  }

  function chooseLocalVideo(video) {
    setFastImportPath(video.path)
    setLocalBrowserOpen(false)
    setError(`Выбран файл для Fast Import: ${video.name}`)
  }

  async function fastImport() {
    const source_path = fastImportPath.trim()
    if (!source_path) return setError('Вставь полный путь к видео, например D:\\Streams\\stream.mp4, или нажми «Обзор диска»')
    if (sourceRequestRef.current) return
    sourceRequestRef.current = true
    setSourceRequest('Создаём проект…')
    try {
      const r = await apiFetch(`${API}/projects/from-path`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_path, storage_mode: fastImportMode })
      })
      if (!r.ok) throw new Error(`Fast import error ${r.status}: ${await responseError(r)}`)
      const p = await r.json()
      activateNewProject(p)
      await applyCurrentSettingsToNewProject(p)
      setError(p.import_warning ? `Проект создан, но есть предупреждение: ${p.import_warning}` : 'Проект создан быстрым импортом. Не перемещай исходный файл до конца обработки.')
      await refreshProjects()
      await refreshAll(p.id)
    } catch (e) {
      setError(`Fast Import не сработал: ${e.message}`)
    } finally {
      sourceRequestRef.current = false
      setSourceRequest('')
    }
  }

  async function twitchImport(autoStart = true) {
    const url = twitchUrl.trim()
    if (!url) return setError('Вставь ссылку Twitch VOD или Live: https://www.twitch.tv/videos/123... или https://www.twitch.tv/channel')
    if (twitchKind === 'vod' && twitchStart && twitchEnd) {
      const parse = (v) => {
        const parts = String(v).trim().split(':').map(Number)
        if (parts.some(n => Number.isNaN(n))) return NaN
        if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if (parts.length === 2) return parts[0] * 60 + parts[1]
        return Number(v)
      }
      if (parse(twitchEnd) <= parse(twitchStart)) return setError('Для VOD время «до» должно быть больше времени «от».')
    }
    if (sourceRequestRef.current) return
    if (autoStart && (busy || twitchSpeedTesting)) return setError('Дождись завершения задачи или останови её в центре задач.')
    sourceRequestRef.current = true
    setSourceRequest('Создаём Twitch-проект…')
    try {
      // In the simple product mode, hidden settings must never keep an old
      // slow downloader choice. Reproduce the proven v10.0.5 Turbo behavior:
      // Auto -> bundled TwitchDownloaderCLI -> aria2 -> yt-dlp fallback.
      const advancedTwitch = productMode === 'pro' || advancedToolsOpen
      const turboThreads = advancedTwitch ? Math.max(1, Math.min(64, Number(twitchThreads || 16))) : 16
      const turboAriaConnections = advancedTwitch ? Math.max(1, Math.min(64, Number(twitchAria2Connections || 16))) : 16
      const turboEngine = advancedTwitch ? (twitchEngine || settings.twitch_download_engine || 'auto') : 'auto'
      const payload = {
        url,
        source_kind: twitchKind,
        vod_start: twitchKind === 'vod' ? twitchStart : '',
        vod_end: twitchKind === 'vod' ? twitchEnd : '',
        live_record_minutes: twitchKind === 'live' ? Number(twitchLiveMinutes || 60) : 60,
        twitch_download_threads: turboThreads,
        twitch_cookies_browser: advancedTwitch ? (twitchCookiesBrowser || 'none') : 'none',
        twitch_format: (twitchFormat || 'best').trim() || 'best',
        twitch_download_engine: turboEngine,
        twitch_quality: (twitchQuality || settings.twitch_quality || 'best').trim() || 'best',
        twitch_fallback_enabled: true,
        twitch_aria2_connections: turboAriaConnections,
        auto_start: autoStart,
      }
      const r = await apiFetch(`${API}/projects/from-twitch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      if (!r.ok) throw new Error(`Twitch import error ${r.status}: ${await responseError(r)}`)
      const p = await r.json()
      activateNewProject(p, 'import')
      if (autoStart) setTaskCenterOpen(true)
      await applyCurrentSettingsToNewProject(p)
      setSourceChoice(twitchKind === 'live' ? 'twitch_live' : 'twitch_vod')
      setError(autoStart
        ? 'Twitch проект создан. Загрузка идёт в фоне. Прогресс и остановка — в центре задач справа сверху. Можно переходить между разделами.'
        : 'Twitch проект создан. Нажми синюю кнопку «Подготовить Twitch источник» в этом блоке или в Project Manager.')
      await refreshProjects()
      await refreshAll(p.id)
    } catch (e) {
      notifyError('Twitch Import не сработал', e)
    } finally {
      sourceRequestRef.current = false
      setSourceRequest('')
    }
  }



  async function runTwitchSpeedTest() {
    if (busy || sourceRequestRef.current || twitchSpeedTesting) return setError('Проверка скорости доступна после завершения текущей операции.')
    const url = twitchUrl.trim() || project?.source_url || project?.twitch?.url || ''
    if (!url) return setError('Вставь Twitch VOD ссылку или открой Twitch-проект.')
    if (twitchKind !== 'vod' && sourceChoice === 'twitch_live') return setError('Speed test доступен для Twitch VOD. Для Live используется streamlink.')
    setTwitchSpeedTesting(true)
    setTwitchSpeedTest(null)
    setError('Запускаю Twitch Turbo speed-test. Приложение попробует быстрые движки и выберет лучший для полного VOD.')
    try {
      let pid = project?.id
      if (!pid || project?.source_type !== 'twitch') {
        const r = await apiFetch(`${API}/projects/from-twitch`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, source_kind: 'vod', vod_start: twitchStart || '00:00:00', vod_end: twitchEnd || '', live_record_minutes: 60, twitch_download_threads: Math.max(1, Math.min(64, Number(twitchThreads || 16))), twitch_cookies_browser: twitchCookiesBrowser || 'none', twitch_format: (twitchFormat || 'best').trim() || 'best', twitch_download_engine: 'auto', twitch_quality: twitchQuality || 'best', twitch_fallback_enabled: true, twitch_aria2_connections: Math.max(1, Math.min(64, Number(twitchAria2Connections || 16))), auto_start: false })
        })
        if (!r.ok) throw new Error(await responseError(r))
        const p = await r.json()
        activateNewProject(p, 'import')
        setSourceChoice('twitch_vod')
        pid = p.id
        await refreshProjects()
      }
      const r2 = await apiFetch(`${API}/projects/${pid}/twitch-speed-test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, source_kind: 'vod', vod_start: twitchStart || '00:00:00', test_seconds: settings.twitch_speed_test_seconds || 45, threads: Math.max(1, Math.min(64, Number(twitchThreads || 16))), quality: twitchQuality || 'best', format: twitchFormat || 'best', aria2_connections: Math.max(1, Math.min(64, Number(twitchAria2Connections || 16))) })
      })
      const data = await safeJsonResponse(r2, { ok: false, message: 'Пустой ответ от backend speed-test.' })
      if (!r2.ok || data?.non_json_response) throw new Error(errorFromPayload(data, `Twitch speed-test HTTP ${r2.status}`))
      setTwitchSpeedTest(data)
      if (data.best_engine) {
        setTwitchEngine(data.best_engine)
        setSettings(st => ({ ...st, twitch_download_engine: data.best_engine }))
      }
      setError(data.recommendation || 'Speed-test завершён.')
      await refreshAll(pid)
    } catch (e) {
      const msg = String(e?.message || e || '')
      if (msg.toLowerCase().includes('json.parse')) {
        setError('Twitch speed-test не сработал: backend вернул не JSON. Перезапусти START_HERE.bat и нажми «Проверить движки». В этой версии UI безопасно показывает ошибку без падения JSON.parse.')
      } else {
        notifyError('Twitch speed-test не сработал', e)
      }
    } finally {
      setTwitchSpeedTesting(false)
    }
  }

  async function prepareTwitchSource() {
    if (!project) return setError('Сначала создай Twitch проект через кнопку «Только создать проект» или «Создать и подготовить».')
    if (project.source_type !== 'twitch') return setError('Текущий проект не Twitch. Открой Twitch-проект или создай его по ссылке Twitch.')
    setError('Подготовка Twitch источника запущена. Приложение скачает/создаст cache для выбранного диапазона, потом можно запускать AI-анализ.')
    await run('twitch-import')
  }

  async function twitchTest10Minutes() {
    if (!twitchUrl.trim()) return setError('Вставь ссылку Twitch VOD, потом нажми «Тест 10 минут».')
    if (twitchKind !== 'vod') return setError('Тест 10 минут доступен для Twitch VOD. Для Live укажи запись 10 минут вручную.')
    if (sourceRequestRef.current) return
    if (busy || twitchSpeedTesting) return setError('Дождись завершения задачи или останови её в центре задач.')
    sourceRequestRef.current = true
    setSourceRequest('Создаём тест на 10 минут…')
    setTwitchStart('00:00:00')
    setTwitchEnd('00:10:00')
    setError('Тестовый диапазон 00:00:00–00:10:00 выбран. Создаю и подготавливаю короткий Twitch-проект.')
    // Call the API directly so state update timing does not affect the payload.
    try {
      const r = await apiFetch(`${API}/projects/from-twitch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: twitchUrl.trim(), source_kind: 'vod', vod_start: '00:00:00', vod_end: '00:10:00', live_record_minutes: 60, twitch_download_threads: Math.max(1, Math.min(64, Number(twitchThreads || 16))), twitch_cookies_browser: twitchCookiesBrowser || 'none', twitch_format: (twitchFormat || 'best').trim() || 'best', twitch_download_engine: twitchEngine || settings.twitch_download_engine || 'auto', twitch_quality: (twitchQuality || settings.twitch_quality || 'best').trim() || 'best', twitch_fallback_enabled: Boolean(twitchFallbackEnabled), twitch_aria2_connections: Math.max(1, Math.min(64, Number(twitchAria2Connections || 16))), auto_start: true })
      })
      if (!r.ok) throw new Error(`Twitch test error ${r.status}: ${await responseError(r)}`)
      const p = await r.json()
      activateNewProject(p, 'import')
      setTaskCenterOpen(true)
      setSourceChoice('twitch_vod')
      await applyCurrentSettingsToNewProject(p)
      await refreshProjects()
      await refreshAll(p.id)
    } catch (e) {
      notifyError('Тест 10 минут не запустился', e)
    } finally {
      sourceRequestRef.current = false
      setSourceRequest('')
    }
  }

  async function persistSettingsForProject(projectId, payload) {
    const r = await apiFetch(`${API}/projects/${projectId}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!r.ok) {
      const body = await r.text()
      throw new Error(`settings error ${r.status}: ${body.slice(0, 500)}`)
    }
    return await r.json()
  }

  async function saveSettings(overrideSettings = null, options = {}) {
    const { refresh = true, announce = true } = options
    const payload = overrideSettings || settings
    if (!project) {
      const safe = persistSafeLocalSettings(payload)
      setSettings(st => ({ ...st, ...safe }))
      if (announce) setError('Настройки сохранены как локальный профиль. Когда создашь проект, они применятся к нему автоматически.')
      return safe
    }
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const savedSettings = await persistSettingsForProject(projectId, payload)
    // Saving A may finish after the user opened B. The server-side save still
    // belongs to A, but it must not overwrite B's React state/local profile.
    if (!isProjectScopeCurrent(scope)) return savedSettings
    const normalized = { ...payload, ...savedSettings }
    persistSafeLocalSettings(normalized)
    setSettings(s => ({ ...s, ...savedSettings }))
    setProject(p => p?.id === projectId ? ({ ...p, settings: { ...(p.settings || {}), ...savedSettings } }) : p)
    setProjects(items => items.map(p => p.id === projectId ? ({ ...p, settings: { ...(p.settings || {}), ...savedSettings } }) : p))
    if (announce) setError('Настройки сохранены в проект и локальный профиль.')
    // Command buttons must not wait for the heavyweight dashboard refresh before
    // their POST is sent.  The caller can refresh immediately after the command
    // has been accepted by the backend instead.
    if (refresh) await refreshAll(projectId, scope)
    return savedSettings
  }






  async function applySmartAutopilot() {
    if (!project) return setError('Сначала создай проект или подготовь Twitch/файл.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/auto-recommendation`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setAutoRec(data)
      setSettings(s => ({ ...s, ...(data.settings || data.settings_patch || {}) }))
      setError(data.summary || data.message || 'Авто-режим подобрал настройки.')
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Авто-режим не смог подобрать настройки', e) }
  }

  async function runSmartPreflight() {
    if (!project?.id) return setError('Сначала создай проект.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/smart-preflight`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setSmartPreflight(data)
      setError(data.can_start ? 'Smart Preflight: можно запускать.' : `Smart Preflight: нельзя запускать. ${data.recommendation || ''}`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Smart Preflight не сработал', e) }
  }

  async function resumeProject() {
    if (!project?.id) return setError('Сначала создай проект.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/resume`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setError(data.started === false ? (data.message || 'Нельзя продолжить.') : 'Продолжение запущено: приложение использует готовые checkpoints и кэш.')
      setActiveStep('analysis')
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Resume не сработал', e) }
  }

  async function saveCacheFingerprint() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/cache-fingerprint`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setCacheReport(data)
      setError('Fingerprint кэша сохранён. Теперь приложение видит, совместим ли старый кэш с текущими настройками.')
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Fingerprint не сохранился', e) }
  }

  async function clearIncompatibleCache() {
    if (!project?.id) return
    if (!confirm('Очистить только несовместимый AI/render cache? Whisper/исходное видео останутся.')) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/clear-incompatible-cache`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setCacheReport(data.cache)
      setError(data.message || `Удалено: ${(data.removed || []).join(', ')}`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Несовместимый cache не очистился', e) }
  }

  async function repairJsonBackups() {
    if (!project?.id) return
    if (!confirm('Восстановить повреждённые JSON-файлы проекта из .bak? Рабочие видеофайлы не трогаются.')) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/repair-json-backups`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `repair ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      setIntegrity(data.integrity || null)
      setError(data.repaired ? `Восстановлено JSON-файлов: ${data.repaired}` : 'Повреждённых JSON-файлов для восстановления не найдено.')
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Восстановление backup не сработало', e) }
  }


  async function exportDebugBundle() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/debug-bundle`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `debug-bundle ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      window.open(`${API}/projects/${projectId}/file/${data.path}${authQuery()}`, '_blank', 'noopener,noreferrer')
      setError(`Debug bundle создан: ${data.size_mb || 0} MB. Видео и большие cache-файлы туда не включены.`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Debug bundle не создался', e) }
  }

  async function refreshAiQualityAudit() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/ai-quality-audit`)
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `ai-quality-audit ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      setAiQualityAudit(data)
      setError(`AI Quality Audit: ${data.score}/100 · ${data.label}`)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Не удалось проверить AI Quality', e) }
  }

  async function backfillExplanations() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/backfill-explanations`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `backfill-explanations ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      setAiQualityAudit(data.audit || data)
      setError(`Объяснения добавлены: candidates ${data.changed?.candidates ?? 0}, segments ${data.changed?.segments ?? 0}.`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Не удалось добавить объяснения', e) }
  }

  async function runProjectDoctor() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/project-doctor`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backfill_explanations: true }) })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `project-doctor ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      setProjectDoctor(data)
      setAiQualityAudit(data.ai_quality || null)
      setProductReadiness(data.product_readiness || null)
      setIntegrity(data.integrity || null)
      setError(`Project Doctor: ${data.actions?.length || 0} действий. ${data.actions?.[0]?.message || 'Проект проверен.'}`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Project Doctor не сработал', e) }
  }


  async function applyDurationAction(action) {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/duration-control`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action }) })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      setSegments(data.segments || [])
      setDurationControl(data.duration_control)
      setError(`Duration Control: действие выполнено — ${action}.`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Duration Control не сработал', e) }
  }

  async function recomputeCreatorPack() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const request = reviewRequestGuardRef.current.begin('creator-pack')
    if (!request) return null
    setNotice({ type: 'info', title: 'Готовлю метаданные…', message: 'Использую текущий монтаж. Дождись результата.' })
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/creator-pack`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      const data = await r.json()
      if (!isProjectScopeCurrent(scope)) return null
      if (data?.ok === false) throw new Error(data.error || 'Недостаточно подтверждённых данных для метаданных.')
      setCreatorPack(data)
      setNotice({ type: data.warning ? 'warning' : 'success', title: 'Метаданные подготовлены', message: data.warning || (data.generation_mode === 'ai_specific' ? 'Текст создан AI по текущему монтажу. Проверь перед публикацией.' : 'Текст подготовлен локально по данным монтажа. Проверь перед публикацией.') })
      return data
    } catch (e) { if (isProjectScopeCurrent(scope)) notifyError('Creator Pack не создался', e) }
    finally { reviewRequestGuardRef.current.end(request) }
  }

  async function refreshYoutubeStatus(refresh = false) {
    try {
      const r = await apiFetch(`${API}/youtube/status${refresh ? '?refresh=true' : ''}`)
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `YouTube status ${r.status}`))
      setYoutubeStatus(data || {})
      return data
    } catch (e) {
      setYoutubeStatus(prev => ({ ...prev, connected: false, error: humanizeErrorMessage(e.message) }))
      return null
    }
  }

  async function uploadYoutubeCredentials(event) {
    const file = event?.target?.files?.[0]
    if (!file) return
    setYoutubeBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await apiFetch(`${API}/youtube/credentials`, { method: 'POST', body: form })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `YouTube credentials ${r.status}`))
      setYoutubeStatus(prev => ({ ...prev, ...data, connected: false }))
      setError('OAuth JSON сохранён. Теперь нажми «Подключить канал».')
    } catch (e) { notifyError('Не удалось сохранить OAuth JSON', e) }
    finally {
      setYoutubeBusy(false)
      if (event?.target) event.target.value = ''
    }
  }

  async function connectYoutube() {
    setYoutubeBusy(true)
    try {
      const r = await apiFetch(`${API}/youtube/connect`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || !data?.authorization_url) throw new Error(errorFromPayload(data, `YouTube connect ${r.status}`))
      const popup = window.open(data.authorization_url, '_blank', 'noopener,noreferrer')
      if (!popup) window.location.href = data.authorization_url
      setError('Google открыл страницу доступа. После подтверждения вернись сюда и нажми «Обновить статус».')
      setTimeout(() => refreshYoutubeStatus(true), 3500)
      setTimeout(() => refreshYoutubeStatus(true), 8000)
    } catch (e) { notifyError('YouTube не подключился', e) }
    finally { setYoutubeBusy(false) }
  }

  async function disconnectYoutube() {
    setYoutubeBusy(true)
    try {
      const r = await apiFetch(`${API}/youtube/disconnect`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `YouTube disconnect ${r.status}`))
      setYoutubeStatus(prev => ({ ...prev, ...data, channel_title: '', channel_id: '' }))
      setError('YouTube-канал отключён от Highlight Studio.')
    } catch (e) { notifyError('Не удалось отключить YouTube', e) }
    finally { setYoutubeBusy(false) }
  }

  function fillYoutubeMetadata(filePath = youtubeForm.file_path) {
    const selected = youtubeVideoOutputs.find(item => item.path === filePath)
    const isShort = selected?.kind === 'short'
    const title = isShort
      ? (factory?.shorts?.[(shortIndexFromOutput(selected) || 1)-1]?.title || creatorPack?.short_titles?.[(shortIndexFromOutput(selected) || 1)-1] || selected?.name || 'Shorts')
      : (creatorPack?.titles?.[0] || selected?.name || 'Нарезка стрима')
    const description = isShort
      ? (creatorPack?.short_summary || creatorPack?.description || '')
      : (creatorPack?.description || '')
    const tags = (creatorPack?.tags || creatorPack?.hashtags || []).map(x => String(x).replace(/^#/, '')).join(', ')
    setYoutubeForm(prev => ({ ...prev, file_path: filePath || prev.file_path, title: String(title).slice(0, 100), description, tags }))
    setError('Название, описание и теги перенесены из Creator Pack.')
  }

  function youtubeItemFromForm(form = youtubeForm) {
    return {
      ...form,
      tags: String(form.tags || '').split(',').map(x => x.trim()).filter(Boolean),
      title: String(form.title || '').trim(),
      description: String(form.description || ''),
    }
  }

  async function startYoutubeUpload(items) {
    if (!project?.id) return setError('Сначала открой проект.')
    if (!youtubeStatus?.connected) return setError('Сначала подключи YouTube-канал.')
    if (!items?.length) return setError('Нет готовых видео для загрузки.')
    setYoutubeBusy(true)
    try {
      startOptimisticJob('youtube-upload')
      const r = await apiFetch(`${API}/projects/${project.id}/youtube-upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `YouTube upload ${r.status}`))
      if (data?.started === false) setError(data.message || 'Другая задача уже выполняется.')
      else setError('Загрузка на YouTube запущена. Прогресс появится в Live Render / Shorts.')
      await refreshAll(project.id)
    } catch (e) { notifyError('Загрузка на YouTube не запустилась', e) }
    finally { setYoutubeBusy(false) }
  }

  async function uploadSelectedYoutube() {
    const item = youtubeItemFromForm()
    if (!item.file_path) return setError('Выбери готовый видеофайл.')
    if (!item.title) return setError('Укажи название видео.')
    await startYoutubeUpload([item])
  }

  async function uploadAllShortsYoutube() {
    if (!youtubeShortOutputs.length) return setError('Сначала создай Shorts.')
    const shortTitles = creatorPack?.short_titles || creatorPack?.titles || []
    const baseDescription = creatorPack?.short_summary || creatorPack?.description || youtubeForm.description || ''
    const tags = String(youtubeForm.tags || (creatorPack?.tags || []).join(',')).split(',').map(x => x.trim()).filter(Boolean)
    const items = youtubeShortOutputs.map((file, index) => ({
      ...youtubeItemFromForm(youtubeForm),
      file_path: file.path,
      title: String(factory?.shorts?.[(shortIndexFromOutput(file) || index+1)-1]?.title || shortTitles[(shortIndexFromOutput(file) || index+1)-1] || `${file.name.replace(/\.[^.]+$/, '')} ${index + 1}`).slice(0, 100),
      description: baseDescription,
      tags,
      notify_subscribers: false,
    }))
    await startYoutubeUpload(items)
  }

  function commandStartMeta(endpoint) {
    const map = {
      render: ['render_prepare', 'Рендер YouTube запущен: готовлю FFmpeg и финальные фрагменты.', 'export'],
      'render-shorts': ['shorts_prepare', 'Shorts export запущен: готовлю вертикальные клипы.', 'export'],
      'render-factory': ['render_factory_prepare', 'Render Factory запущен: собираю версии ролика.', 'export'],
      'youtube-upload': ['youtube_upload_prepare', 'Загрузка на YouTube запущена.', 'export'],
      metadata: ['metadata_prepare', 'Metadata запущена: готовлю YouTube-пакет.', 'export'],
      analyze: ['analysis_prepare', 'AI-анализ запущен: готовлю pipeline.', 'analysis'],
      one_click: ['one_click_prepare', 'Анализ запускается: сохраняю настройки и проверяю проект.', 'analysis'],
      'twitch-import': ['twitch_import', 'Twitch import запущен: готовлю источник.', 'import'],
    }
    return map[endpoint] || [endpoint, `Команда ${endpoint} запущена.`, activeStep]
  }

  function startOptimisticJob(endpoint) {
    const [stage, message, step] = commandStartMeta(endpoint)
    const now = Date.now() / 1000
    const optimisticStatus = {
      state: 'queued',
      progress: 1,
      message,
      stage,
      updated_at: now,
      started_at: now,
      elapsed_seconds: 0,
      progress_source: 'optimistic_ui',
    }
    optimisticJobRef.current = { endpoint, stage, status: optimisticStatus, keepUntil: Date.now() + 6000 }
    setStatus(prev => ({ ...(prev || {}), ...optimisticStatus }))
    setProgressExpanded(true)
    setTaskCenterOpen(true)
    if (step) setActiveStep(step)
    setError(message)
  }

  function confirmReanalysis(label = 'Повторный анализ') {
    if (!analysisCurrent) return true
    return window.confirm(`${label} пересчитает AI-кандидаты и автоматическую нарезку. Ручные правки текущего результата могут стать устаревшими. Исходное видео и настройки проекта сохранятся. Продолжить?`)
  }

  async function run(endpoint) {
    if (!project) {
      setError('Сначала загрузи видео.')
      return
    }
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      startOptimisticJob(endpoint)
      await saveSettings(null, { refresh: false, announce: false })
      if (!isProjectScopeCurrent(scope)) return
      const r = await apiFetch(`${API}/projects/${projectId}/${endpoint}`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `${endpoint} error ${r.status}`))
      if (data?.started === false) {
        setError(data.message || 'Задача уже запущена')
      } else if (data?.ok === false) {
        setError(data.error || data.message || `Команда ${endpoint} выполнена, но результата пока нет.`)
      } else {
        const [, message] = commandStartMeta(endpoint)
        setError(`${message} Прогресс и лог теперь обновляются в реальном времени.`)
      }
      await refreshAll(projectId, scope)
      setTimeout(() => refreshAll(projectId, scope), 700)
      setTimeout(() => refreshAll(projectId, scope), 1800)
      setTimeout(() => refreshAll(projectId, scope), 3500)
    } catch (e) {
      notifyError(`Команда ${endpoint} не сработала`, e)
      await refreshAll(projectId, scope)
    }
  }

  async function submitShort(index, payload, render = false) {
    if (!project || busy || shortRequestRef.current) throw new Error('Дождись завершения текущей задачи.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    shortRequestRef.current = true
    try {
      await saveSettings(null, { refresh: false, announce: false })
      if (!isProjectScopeCurrent(scope)) throw new Error('Проект был переключён. Открой его для продолжения.')
      const response = await apiFetch(`${API}/projects/${projectId}/shorts/${index}${render ? '/render' : ''}`, {
        method: render ? 'POST' : 'PUT', headers: {'Content-Type':'application/json'}, body:JSON.stringify(payload),
      })
      const data = await safeJsonResponse(response, null)
      if (!response.ok || data?.ok === false || data?.started === false) throw new Error(errorFromPayload(data, data?.message || 'Не удалось сохранить ролик.'))
      const candidate = data?.candidate || {...(factory?.shorts?.[index-1] || {}),...payload,render_dirty:true}
      if (isProjectScopeCurrent(scope)) {
        setFactory(previous => previous ? ({...previous,shorts:(previous.shorts || []).map((row,offset)=>offset===index-1?candidate:row)}) : previous)
        if (render) {
          const now = Date.now()/1000
          setTaskCenterOpen(true)
          setStatus({state:'queued',progress:1,stage:'shorts_single_queued',message:`Собираем ролик ${index}`,started_at:now,updated_at:now})
        }
        void refreshAll(projectId,scope)
      }
      return candidate
    } finally { shortRequestRef.current = false }
  }

  async function generateShorts() {
    if (!project || busy || shortRequestRef.current) throw new Error('Дождись завершения текущей задачи.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    shortRequestRef.current = true
    try {
      await saveSettings(null, {refresh:false,announce:false})
      if (!isProjectScopeCurrent(scope)) return
      const response = await apiFetch(`${API}/projects/${projectId}/render-shorts`, {method:'POST'})
      const data = await safeJsonResponse(response,null)
      if (!response.ok || data?.started === false || data?.ok === false) throw new Error(errorFromPayload(data,data?.message || 'Не удалось запустить сборку.'))
      if (isProjectScopeCurrent(scope)) {
        setTaskCenterOpen(true)
        setStatus({state:'queued',progress:1,stage:'shorts_queued',message:'Готовим подборку Shorts',started_at:Date.now()/1000})
        void refreshAll(projectId,scope)
      }
    } finally {shortRequestRef.current=false}
  }


  async function runOneClickPipeline() {
    if (!project) return setError('Сначала создай проект: Twitch VOD, Fast Import или файл с ПК.')
    if (!sourceReady) return setNotice({ type: 'warning', title: 'Источник ещё не готов', message: 'Подготовь видео перед запуском анализа.', action: { label: 'Открыть источник', id: 'open-source' } })
    if (!formatConfirmed) return setNotice({ type: 'warning', title: 'Сначала выбери формат', message: 'Подтверди формат и желаемую длительность ролика.', action: { label: 'Выбрать формат', id: 'open-format' } })
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      // Give immediate visual feedback before any network round-trip.  v10.15.4
      // waited for a full dashboard refresh here, which could make the button
      // look dead for several seconds while Ollama/preflight probes were running.
      startOptimisticJob('one_click')
      // Preserve the selected task preset.  Only clamp dangerous local-Ollama values
      // so one-click does not silently erase IRL/sport/podcast/Shorts choices.
      const prepared = prepareOneClickSettings(settings)
      setSettings(prepared)
      await saveSettings(prepared, { refresh: false, announce: false })
      if (!isProjectScopeCurrent(scope)) return
      const r = await apiFetch(`${API}/projects/${projectId}/one-click`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `one-click error ${r.status}`))
      if (data?.started === false) setError(`One-click не запустился: ${data.message || 'уже идёт другая задача'}`)
      else setError('Анализ запущен. Прогресс и текущий этап — в центре задач справа сверху; рендер останется отдельным шагом после Review Studio.')
      setActiveStep('analysis')
      await refreshAll(projectId, scope)
      setTimeout(() => refreshAll(projectId, scope), 650)
      setTimeout(() => refreshAll(projectId, scope), 1800)
    } catch (e) {
      optimisticJobRef.current = null
      setStatus(previous => previous?.progress_source === 'optimistic_ui'
        ? { state: 'idle', progress: 0, message: 'Анализ не запущен', stage: 'idle', progress_source: 'client_error' }
        : previous)
      notifyError('Анализ не запустился', e)
      await refreshAll(projectId, scope)
    }
  }

  async function runSimple(endpoint) {
    if (!project) return setError('Сначала загрузи видео.')
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      if (endpoint !== 'cancel') await saveSettings(null, { refresh: false, announce: false })
      if (!isProjectScopeCurrent(scope)) return
      const r = await apiFetch(`${API}/projects/${projectId}/${endpoint}`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok) throw new Error(errorFromPayload(data, `${endpoint} error ${r.status}`))
      await refreshAll(projectId, scope)
    } catch (e) {
      notifyError(`Команда ${endpoint} не сработала`, e)
    }
  }






  function setEditModeSynced(edit_mode) {
    setSettings(s => ({
      ...s,
      edit_mode,
      // If an advanced IRL A/B mode is selected, enable the full IRL profile too.
      // This keeps backend prompt, bonuses and Adaptive Auto in sync.
      content_type: edit_mode.startsWith('IRL') && (!s.content_type || s.content_type === 'Auto') ? 'IRL стрим' : s.content_type
    }))
  }

  function setContentTypeSynced(content_type) {
    setSettings(s => ({
      ...s,
      content_type,
      edit_mode: content_type.startsWith('IRL') && (!s.edit_mode || s.edit_mode === 'Сбалансированный') ? 'IRL история' : s.edit_mode
    }))
  }


  async function applyTaskPreset(kind) {
    const preset = TASK_PRESET_MAP[kind]
    if (!preset) return setError('Такой task-пресет не найден.')
    const next = { ...settings, ...preset.settings, ai_engine: 'ollama' }
    setSettings(next)
    if (project) {
      const projectId = project.id
      const scope = captureProjectScope(projectId)
      try {
        const r = await apiFetch(`${API}/projects/${projectId}/task-preset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: kind })
        })
        if (!r.ok) throw new Error(await responseError(r))
        const data = await r.json()
        setProjects(items => items.map(pj => pj.id === projectId ? ({ ...pj, settings: { ...(pj.settings || {}), ...(data.settings || {}) } }) : pj))
        if (!isProjectScopeCurrent(scope)) return
        setSettings(st => ({ ...st, ...(data.settings || {}) }))
        setProject(pj => pj?.id === projectId ? ({ ...pj, settings: { ...(pj.settings || {}), ...(data.settings || {}) } }) : pj)
        setError(`Task-пресет «${data.label || preset.label}» сохранён. Теперь нажми «Собрать нарезку».`)
      } catch (e) {
        setError(`Task-пресет применён на экране, но не сохранился: ${e.message}`)
      }
    } else {
      setError(`Task-пресет «${preset.label}» применён. Создай проект, чтобы сохранить его на диск.`)
    }
  }

  function prepareOneClickSettings(base = settings) {
    const analysisProfile = ['fast', 'balanced', 'quality'].includes(base.analysis_profile)
      ? base.analysis_profile
      : base.analysis_profile === 'safe' ? 'fast' : 'balanced'
    return {
      ...base,
      analysis_profile: analysisProfile,
      ai_engine: 'ollama',
      ai_batch_size: Math.max(1, Math.min(3, Number(base.ai_batch_size || 1))),
      micro_batch_size: Math.max(1, Math.min(8, Number(base.micro_batch_size || 1))),
      ai_retry_count: Math.max(1, Math.min(3, Number(base.ai_retry_count || 2))),
      ollama_timeout: Math.max(900, Number(base.ollama_timeout || 900)),
      ollama_keep_alive: base.ollama_keep_alive || '5m',
      metadata_max_segments: Math.max(1, Math.min(25, Number(base.metadata_max_segments || 12))),
    }
  }






  function sanitizeSegments(next) {
    return [...next]
      .map(s => {
        const start = Math.max(0, Number(s.start) || 0)
        const end = Math.max(start + 0.1, Number(s.end) || start + 0.1)
        return { ...s, start, end, score: Number(s.score) || 0 }
      })
      .sort((a, b) => a.start - b.start)
      .map((s, i) => ({ ...s, id: i + 1 }))
  }

  async function updateSegments(next, { recordHistory = true } = {}) {
    if (!project?.id) {
      setError('Сначала открой проект.')
      return false
    }
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const clean = sanitizeSegments(next)
    const previous = segments
    const expectedRevision = segmentsRevision
    const request = reviewRequestGuardRef.current.begin('segments')
    if (!request) return false
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (expectedRevision) headers['X-Segments-Revision'] = expectedRevision
      const r = await apiFetch(`${API}/projects/${projectId}/segments`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(clean)
      })
      if (r.status === 409 || r.status === 428) {
        reviewRequestGuardRef.current.end(request)
        if (isProjectScopeCurrent(scope)) {
          await refreshAll(projectId, scope)
          setNotice({ type: 'error', title: 'Монтаж изменился в другом окне', message: 'Данные обновлены. Повтори свою правку на актуальной версии монтажа.' })
        }
        return false
      }
      if (!r.ok) throw new Error(await responseError(r))
      const saved = await safeJsonResponse(r, clean)
      if (!isProjectScopeCurrent(scope)) return false
      const normalized = Array.isArray(saved) ? saved : clean
      const newRevision = r.headers.get('X-Segments-Revision') || ''
      if (recordHistory && JSON.stringify(normalized) !== JSON.stringify(previous)) {
        setSegmentUndoStack(stack => [...stack.slice(-29), previous])
        setSegmentRedoStack([])
      }
      setSegments(normalized)
      if (newRevision) setSegmentsRevision(newRevision)
      reviewRequestGuardRef.current.end(request)
      await refreshAll(projectId, scope)
      return normalized
    } catch (e) {
      if (isProjectScopeCurrent(scope)) {
        setSegments(previous)
        setError(`Финальный монтаж не сохранился: ${humanizeErrorMessage(e.message)}`)
      }
      return false
    } finally { reviewRequestGuardRef.current.end(request) }
  }


  async function sendFeedback(item, label, note = 'manual') {
    if (!project?.id) return false
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const response = await apiFetch(`${API}/projects/${projectId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item, label, note })
      })
      if (!response.ok) throw new Error(await responseError(response))
      if (!isProjectScopeCurrent(scope)) return false
      await refreshAll(projectId, scope)
      return true
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Feedback не сохранился: ${humanizeErrorMessage(e.message)}`)
      return false
    }
  }

  function segmentIndexFor(item) {
    return findSegmentIndex(segments, item)
  }

  function isInMontage(item) { return segmentIndexFor(item) >= 0 }

  function candidateMutationKey(item, projectId = project?.id) {
    return `${projectId || 'no-project'}:${sourceCandidateKey(item || {})}`
  }

  function isCandidateAdding(item) {
    return pendingCandidateKeys.includes(candidateMutationKey(item))
  }

  function advanceReviewCandidate(current) {
    const pool = filteredCandidates.filter(item => !isInMontage(item))
    if (!pool.length) {
      setPreviewClip(null)
      return
    }
    const key = sourceCandidateKey(current)
    const index = pool.findIndex(item => sourceCandidateKey(item) === key)
    const next = pool[index >= 0 ? Math.min(index + 1, pool.length - 1) : 0]
    if (next && sourceCandidateKey(next) !== key) preview(next, 'candidate')
    else if (pool.length === 1 && sourceCandidateKey(pool[0]) !== key) preview(pool[0], 'candidate')
    else setPreviewClip(null)
  }

  async function addCandidate(c) {
    if (!c || isInMontage(c)) {
      setError('Этот момент уже находится в итоговой нарезке.')
      return false
    }
    if (!project?.id) {
      setError('Сначала открой проект.')
      return false
    }
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const candidate = withSourceCandidateKey(c)
    const pendingKey = candidateMutationKey(candidate, projectId)
    if (pendingCandidateKeys.includes(pendingKey)) return false
    if (findOverlapIndex(segments, candidate) >= 0) {
      setError('Похожий фрагмент уже есть в итоговой нарезке. Измени границы существующего фрагмента вместо создания дубля.')
      return false
    }
    const request = reviewRequestGuardRef.current.begin('segments')
    if (!request) {
      setNotice({ type: 'info', title: 'Сохраняю предыдущую правку…', message: 'Дождись завершения сохранения и повтори добавление.' })
      return false
    }
    setPendingCandidateKeys(keys => keys.includes(pendingKey) ? keys : [...keys, pendingKey])
    setNotice({
      type: 'info',
      title: 'Добавляю момент…',
      message: 'Команда принята. Фрагмент сохраняется в итоговую нарезку; повторное нажатие временно заблокировано.',
    })
    try {
      const response = await apiFetch(`${API}/projects/${projectId}/segments/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: candidate, expected_revision: segmentsRevision || '' }),
      })
      const data = await safeJsonResponse(response, {})
      if (!isProjectScopeCurrent(scope)) return false
      if (response.status === 409 && data?.detail?.current_revision) {
        reviewRequestGuardRef.current.end(request)
        await refreshAll(projectId, scope)
        if (isProjectScopeCurrent(scope)) setError(data.detail.message || 'Монтаж изменился. Данные обновлены — повтори добавление момента.')
        return false
      }
      if (!response.ok || data?.ok === false) throw new Error(errorFromPayload(data, `HTTP ${response.status}`))
      if (!isProjectScopeCurrent(scope)) return false
      const saved = Array.isArray(data?.segments) ? data.segments : segments
      setSegments(saved)
      if (data?.segments_revision) setSegmentsRevision(data.segments_revision)
      setSegmentUndoStack(stack => [...stack.slice(-29), segments])
      setSegmentRedoStack([])
      setActiveTab('candidates')
      setReviewTabTouched(true)
      advanceReviewCandidate(candidate)
      setNotice({ type: 'success', title: data?.added === false ? 'Момент уже был добавлен' : 'Добавлено в итоговую нарезку', message: data?.message || 'Изменение сохранено.' })
      return saved
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Не удалось добавить момент: ${humanizeErrorMessage(e.message)}`)
      return false
    } finally {
      reviewRequestGuardRef.current.end(request)
      setPendingCandidateKeys(keys => keys.filter(key => key !== pendingKey))
    }
  }

  async function removeSegment(i) {
    const item = segments[i]
    if (!project?.id || !item) return false
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const request = reviewRequestGuardRef.current.begin('segments')
    if (!request) {
      setNotice({ type: 'info', title: 'Сохраняю предыдущую правку…', message: 'Дождись завершения сохранения.' })
      return false
    }
    try {
      const response = await apiFetch(`${API}/projects/${projectId}/segments/remove`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: { id: item.id, start: item.start, end: item.end }, expected_revision: segmentsRevision }),
      })
      const data = await safeJsonResponse(response, {})
      if (!isProjectScopeCurrent(scope)) return false
      if (response.status === 409 || response.status === 428) {
        reviewRequestGuardRef.current.end(request)
        await refreshAll(projectId, scope)
        if (isProjectScopeCurrent(scope)) setError(data?.detail?.message || 'Монтаж обновлён. Повтори удаление.')
        return false
      }
      if (!response.ok || !Array.isArray(data.segments)) throw new Error(errorFromPayload(data, `HTTP ${response.status}`))
      setSegmentUndoStack(stack => [...stack.slice(-29), segments])
      setSegmentRedoStack([])
      setSegments(data.segments)
      setSegmentsRevision(data.segments_revision)
      setPreviewClip(null)
      setSelectedTimelineId(null)
      setNotice({ type: 'success', title: 'Фрагмент удалён', message: 'Изменение сохранено. Можно отменить удаление.' })
      return data.segments
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Не удалось удалить фрагмент: ${humanizeErrorMessage(e.message)}`)
      return false
    } finally { reviewRequestGuardRef.current.end(request) }
  }

  async function upsertAdjustedClip(item, patch) {
    if (!item) return false
    const idx = segmentIndexFor(item)
    const base = withSourceCandidateKey({ ...item, ...patch })
    if (findOverlapIndex(segments, base, idx) >= 0) {
      setError('Новые границы пересекаются с другим фрагментом. Сначала сократи соседний момент.')
      return false
    }
    const next = idx >= 0 ? segments.map((s, i) => i === idx ? { ...s, ...patch } : s) : [...segments, base]
    const saved = await updateSegments(next)
    if (saved) {
      const savedIndex = findSegmentIndex(saved, base)
      const updated = savedIndex >= 0 ? saved[savedIndex] : base
      setPreviewClip(previous => ({ ...updated, type: previous?.type || item.type || (idx >= 0 ? 'final' : 'candidate') }))
      setSelectedTimelineId(updated.id ?? selectedTimelineId)
      setActiveTab('final')
    }
    return saved
  }

  async function applyTrimDraft() {
    if (!selectedPreview) return
    const maxDuration = Number(project?.duration_seconds || autoRec?.probe?.duration_seconds || autoRec?.duration_seconds || 0)
    const parsed = parseTrimBounds(trimDraft.start, trimDraft.end, maxDuration)
    if (!parsed.ok) {
      setError(parsed.message)
      return
    }
    const saved = await upsertAdjustedClip(selectedPreview, { start: parsed.start, end: parsed.end })
    if (saved) setError('Границы фрагмента сохранены.')
  }

  async function rejectClip(item) {
    if (!item) return
    const key = sourceCandidateKey(item)
    const idx = segmentIndexFor(item)
    const removed = idx >= 0 ? await removeSegment(idx) : true
    if (!removed) return
    setRejectedCandidateKeys(keys => keys.includes(key) ? keys : [...keys, key])
    setExpandedPreviewKey(null)
    await sendFeedback(item, 'bad', 'review_reject')
    advanceReviewCandidate(item)
  }

  async function undoSegments() {
    const previous = segmentUndoStack.at(-1)
    if (!previous) return
    const current = segments
    const saved = await updateSegments(previous, { recordHistory: false })
    if (!saved) return
    setSegmentUndoStack(stack => stack.slice(0, -1))
    setSegmentRedoStack(stack => [...stack.slice(-29), current])
  }

  async function redoSegments() {
    const next = segmentRedoStack.at(-1)
    if (!next) return
    const current = segments
    const saved = await updateSegments(next, { recordHistory: false })
    if (!saved) return
    setSegmentRedoStack(stack => stack.slice(0, -1))
    setSegmentUndoStack(stack => [...stack.slice(-29), current])
  }

  function clipKey(item, type = 'candidate') {
    if (!item) return ''
    return `${type}-${item.id ?? item.start ?? 'clip'}-${Number(item.start || 0).toFixed(2)}`
  }

  function clipDuration(item) {
    if (!item) return 0
    return Math.max(0, (Number(item.end) || 0) - (Number(item.start) || 0))
  }


  function preview(item, type = 'candidate') {
    if (!item) return
    const next = { ...item, type }
    setPreviewClip(next)
    setSelectedTimelineId(item.id ?? `${type}-${item.start}`)
    setExpandedPreviewKey(clipKey(item, type))
    if (type === 'final') setActiveTab('final')
  }


  async function clearCache() {
    if (!project?.id) return
    if (!confirm('Очистить AI/preview/render кэш проекта? Исходное видео, сегменты и настройки останутся.')) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/clear-cache`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `clear-cache ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      const removed = data?.removed || []
      const failed = data?.failed || []
      setError(failed.length
        ? `Кэш очищен частично. Удалено: ${removed.join(', ') || 'ничего'}. Не удалено: ${failed.map(item => item.name || item.path || item).join(', ')}`
        : `Кэш очищен: ${removed.join(', ') || 'ничего не найдено'}`)
      await refreshAll(projectId, scope)
      return data
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Кэш не очистился: ${humanizeErrorMessage(e.message)}`)
      return null
    }
  }

  async function exportProject() {
    if (!project?.id) return
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    try {
      const r = await apiFetch(`${API}/projects/${projectId}/export`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `export ${r.status}`))
      if (!isProjectScopeCurrent(scope)) return null
      window.open(`${API}/projects/${projectId}/file/${data.path}${authQuery()}`, '_blank', 'noopener,noreferrer')
      await refreshAll(projectId, scope)
      return data
    } catch (e) {
      if (isProjectScopeCurrent(scope)) setError(`Экспорт не создался: ${humanizeErrorMessage(e.message)}`)
      return null
    }
  }

  async function saveMyBestSettings() {
    if (!project) {
      try {
        const r = await apiFetch(`${API}/my-best-settings/save`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'Мой рабочий пресет', settings })
        })
        const data = await safeJsonResponse(r, null)
        if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `save best ${r.status}`))
        setError('Мой рабочий пресет сохранён локально. Когда создашь проект, его можно применить в настройках.')
        setBestProfile(data.profile || null)
      } catch (e) { notifyError('Рабочий пресет не сохранился', e) }
      return
    }
    try {
      await saveSettings()
      const r = await apiFetch(`${API}/projects/${project.id}/my-best-settings/save`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `save project best ${r.status}`))
      setBestProfile(data.profile || null)
      setQualityLock(data.quality_lock || null)
      setError('Мой рабочий пресет сохранён. Включена защита качества: изменение AI-настроек будет помечаться предупреждением.')
      await refreshAll(project.id)
    } catch (e) { notifyError('Рабочий пресет не сохранился', e) }
  }

  async function applyMyBestSettings() {
    if (!project) {
      try {
        const r = await apiFetch(`${API}/my-best-settings`)
        const data = await safeJsonResponse(r, null)
        if (!r.ok || data?.ok === false || !data?.exists) throw new Error(errorFromPayload(data, 'Рабочий пресет пока не сохранён'))
        const safe = persistSafeLocalSettings(data.profile?.settings || {})
        setSettings(st => ({ ...st, ...safe }))
        setBestProfile(data.profile || null)
        return setError('Мой рабочий пресет применён к локальному профилю. Создай проект — эти настройки применятся автоматически.')
      } catch (e) { return notifyError('Рабочий пресет не применился', e) }
    }
    try {
      const r = await apiFetch(`${API}/projects/${project.id}/my-best-settings/apply`, { method: 'POST' })
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, `apply best ${r.status}`))
      setSettings(st => ({ ...st, ...(data.settings || {}) }))
      setQualityLock(data.quality_lock || null)
      setError(data.message || 'Мой рабочий пресет применён.')
      await refreshAll(project.id)
    } catch (e) { notifyError('Рабочий пресет не применился', e) }
  }

  async function openFinalVideo() {
    const filePath = finalSuccess?.video?.path || 'outputs/highlight_final.mp4'
    const encodedPath = filePath.split('/').map(encodeURIComponent).join('/')
    if (!project) return
    try {
      if (desktopInfo?.desktop) {
        const r = await apiFetch(`${API}/projects/${project.id}/native-path/${encodedPath}`)
        const data = await safeJsonResponse(r, null)
        if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, 'Готовый файл не найден'))
        const opened = await openNativePath(data.path)
        if (!opened?.ok) throw new Error(opened?.message || 'Windows не смог открыть видео')
        return
      }
      window.open(`${API}/projects/${project.id}/file/${encodedPath}${authQuery()}`, '_blank', 'noopener,noreferrer')
    } catch (e) {
      notifyError('Видео не открылось', e)
    }
  }

  async function showProjectFolder() {
    if (!project) return
    try {
      const r = await apiFetch(`${API}/projects/${project.id}/open-folder`)
      const data = await safeJsonResponse(r, null)
      if (!r.ok || data?.ok === false) throw new Error(errorFromPayload(data, 'Папка проекта недоступна'))
      if (desktopInfo?.desktop) {
        const opened = await openNativePath(data.path)
        if (!opened?.ok) throw new Error(opened?.message || 'Windows не смог открыть папку')
        setError('Папка проекта открыта.')
      } else {
        setError(`Папка проекта: ${data.path}`)
      }
    } catch (e) {
      notifyError('Папка проекта не открылась', e)
    }
  }

  async function recomputeReport(endpoint) {
    if (!project) return
    try {
      const r = await apiFetch(`${API}/projects/${project.id}/${endpoint}`, { method: 'POST' })
      if (!r.ok) throw new Error((await r.text()).slice(0, 300))
      await refreshAll()
    } catch (e) {
      setError(`Отчёт ${endpoint} не пересчитался: ${e.message}`)
    }
  }

  const selectedPreview = previewClip || filteredCandidates[0] || segments[0] || null
  useEffect(() => {
    if (!project?.id || !selectedPreview) {
      setClipPreview({ url: '', loading: false, error: '', key: '' })
      return
    }
    const projectId = project.id
    const scope = captureProjectScope(projectId)
    const controller = new AbortController()
    const start = Math.max(0, Number(selectedPreview.start) || 0)
    const end = Math.max(start + 0.5, Number(selectedPreview.end) || start + 30)
    const key = `${projectId}:${start.toFixed(3)}:${end.toFixed(3)}:${clipPreviewReload}`
    clipPreviewErrorRetries.current = 0
    setClipPreview({ url: '', loading: true, error: '', key })
    apiFetch(`${API}/projects/${projectId}/clip-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start, end, force: clipPreviewReload > 0 }),
      signal: controller.signal,
    }).then(async response => {
      const data = await safeJsonResponse(response, null)
      if (!response.ok || !data?.url) throw new Error(errorFromPayload(data, 'Не удалось подготовить фрагмент'))
      if (!isProjectScopeCurrent(scope)) return
      setClipPreview({ url: `${data.url}${authQuery()}${authQuery() ? '&' : '?'}v=${data.cache_key}`, loading: false, error: '', key })
    }).catch(err => {
      if (err?.name === 'AbortError' || !isProjectScopeCurrent(scope)) return
      setClipPreview({ url: '', loading: false, error: err.message || 'Ошибка плеера', key })
    })
    return () => controller.abort()
    // scope helpers are ref-backed; project id/selected clip/reload own this request lifecycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, selectedPreview, clipPreviewReload])

  useEffect(() => {
    const v = reviewVideoRef.current
    if (!v || !clipPreview.url) return
    v.pause()
    v.load()
  }, [clipPreview.url])
  useEffect(() => {
    if (!selectedPreview) return
    setTrimDraft({ start: String(Number(selectedPreview.start || 0).toFixed(2)), end: String(Number(selectedPreview.end || 0).toFixed(2)) })
  }, [selectedPreview])

  useEffect(() => {
    if (activeStep !== 'review') return
    const handler = (event) => {
      const tag = event.target?.tagName
      if (['INPUT','TEXTAREA','SELECT'].includes(tag)) return
      const list = activeTab === 'final' ? segments : filteredCandidates
      const selectedKey = sourceCandidateKey(selectedPreview || {})
      const currentIndex = Math.max(0, list.findIndex(x => sourceCandidateKey(x) === selectedKey))
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redoSegments() : undoSegments(); return }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); redoSegments(); return }
      if (event.code === 'Space') { event.preventDefault(); const v=reviewVideoRef.current; if(v) v.paused ? v.play().catch(()=>{}) : v.pause(); return }
      if (event.key === 'ArrowRight' && list.length) { event.preventDefault(); setPreviewClip(list[Math.min(list.length-1,currentIndex+1)]); return }
      if (event.key === 'ArrowLeft' && list.length) { event.preventDefault(); setPreviewClip(list[Math.max(0,currentIndex-1)]); return }
      if (event.key.toLowerCase() === 'a' && selectedPreview) { event.preventDefault(); addCandidate(selectedPreview); return }
      if (event.key.toLowerCase() === 'x' && selectedPreview) { event.preventDefault(); rejectClip(selectedPreview); return }
      if (event.key === '[' && selectedPreview) { event.preventDefault(); upsertAdjustedClip(selectedPreview,{start:Math.max(0,Number(selectedPreview.start||0)-1)}); return }
      if (event.key === ']' && selectedPreview) { event.preventDefault(); upsertAdjustedClip(selectedPreview,{end:Number(selectedPreview.end||0)+1}); }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // The listed state values recreate the handler; action functions intentionally use that render's snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStep, activeTab, segments, filteredCandidates, selectedPreview, segmentUndoStack, segmentRedoStack])

  const workflowSteps = [
    {
      id: 'import',
      title: 'Источник',
      hint: sourceReady ? 'Видео готово' : project ? 'Подготовь видео' : 'Добавь видео',
      icon: <Upload size={18}/>,
      completed: sourceReady,
      locked: false,
    },
    {
      id: 'style',
      title: 'Формат',
      hint: !sourceReady ? 'После источника' : formatConfirmed ? settings.task_preset_label || 'Выбран' : 'Выбери результат',
      icon: <Wand2 size={18}/>,
      completed: formatConfirmed,
      locked: !sourceReady,
      blockedReason: 'Сначала добавь и подготовь исходное видео.',
    },
    {
      id: 'analysis',
      title: 'Анализ',
      hint: busy && activeStep === 'analysis' ? `${Math.round(progressValue)}%` : analysisCurrent ? `${candidates.length} моментов · завершён` : !formatConfirmed ? 'После формата' : 'Готов к запуску',
      icon: <Sparkles size={18}/>,
      completed: analysisCurrent,
      locked: !sourceReady || !formatConfirmed,
      blockedReason: !sourceReady ? 'Сначала подготовь источник.' : 'Сначала подтверди формат результата.',
    },
    {
      id: 'review',
      title: 'Монтаж',
      hint: analysisCurrent ? (segments.length ? `AI-нарезка: ${segments.length}` : candidates.length ? 'Есть альтернативы' : 'Анализ завершён · 0 моментов') : 'После анализа',
      icon: <Scissors size={18}/>,
      completed: segments.length > 0,
      locked: !analysisCurrent,
      blockedReason: 'Сначала заверши анализ. После завершения монтаж можно открыть вручную даже если кандидатов 0.',
    },
    {
      id: 'export',
      title: 'Экспорт',
      hint: finalReady ? 'Видео готово' : segments.length ? 'Готов к рендеру' : 'После итоговой нарезки',
      icon: <Download size={18}/>,
      completed: finalReady,
      locked: segments.length === 0,
      blockedReason: 'Сначала добавь хотя бы один фрагмент в монтаж.',
    },
  ]

  async function openWorkflowStep(step) {
    if (!step?.id) return
    if (!step.locked) {
      setActiveStep(step.id)
      setMobileNavOpen(false)
      return
    }

    // A long background job can finish between two React polls. If the user
    // explicitly clicks a locked later step, ask the backend once before saying
    // no. This keeps navigation manual while preventing stale UI from trapping
    // the user on Analysis after the persisted analysis is already complete.
    if (project?.id && ['review', 'export'].includes(step.id)) {
      const projectId = project.id
      const scope = captureProjectScope(projectId)
      const data = await refreshAll(projectId, scope)
      if (data && isProjectScopeCurrent(scope)) {
        const fresh = data.freshness || data.project?.freshness || {}
        const analysisReady = Boolean(fresh.analysis_current ?? fresh.candidates_current ?? false)
        const segmentCount = Array.isArray(data.segments) ? data.segments.length : 0
        if (step.id === 'review' && analysisReady) {
          setFormatConfirmed(true)
          localStorage.setItem(`highlightStudioFormatConfirmed:${projectId}`, 'true')
          setActiveStep('review')
          setMobileNavOpen(false)
          return
        }
        if (step.id === 'export' && analysisReady && segmentCount > 0) {
          setFormatConfirmed(true)
          setActiveStep('export')
          setMobileNavOpen(false)
          return
        }
      }
    }

    setNotice({
      type: 'warning',
      title: `${step.title} пока недоступен`,
      message: step.blockedReason,
    })
  }

  function toggleProductSidebar() {
    setSidebarCompact(value => {
      const next = !value
      persistLayoutPreference(SIDEBAR_COMPACT_KEY, String(next))
      return next
    })
  }

  function handleNoticeAction(actionId) {
    if (actionId === 'retry-backend') refreshProjects()
    if (actionId === 'open-source') beginProject('local', true)
    if (actionId === 'open-format') setActiveStep('style')
    if (actionId === 'open-projects') setActiveStep('projects')
    if (actionId === 'retry-youtube') uploadSelectedYoutube()
    setNoticeState(null)
  }

  function StepHeader({ eyebrow, title, description, children }) {
    return <div className="stepHero">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {children && <div className="stepHeroActions">{children}</div>}
    </div>
  }







  function currentPhaseIndex() {
    const stage = String(status.stage || '').toLowerCase()
    const message = String(status.message || '').toLowerCase()
    const joined = `${stage} ${message}`
    if (!project) return 0
    if (joined.includes('twitch') || joined.includes('upload') || joined.includes('import')) return 0
    if (joined.includes('preflight') || joined.includes('провер')) return 1
    if (joined.includes('transcription') || joined.includes('транск')) return 2
    if (joined.includes('ai') || joined.includes('micro')) return 3
    if (joined.includes('visual') || joined.includes('ocr') || joined.includes('audio')) return 4
    if (joined.includes('render') || joined.includes('shorts') || joined.includes('metadata')) return 5
    if (finalReady) return 6
    if (segments.length) return 5
    if (candidates.length) return 4
    return 1
  }

  function renderPhaseRail() {
    const phases = [
      ['Источник', project ? 'готов' : 'нет проекта'],
      ['Проверка', preRenderReport?.ready ? 'готова' : systemCheck?.ok ? 'система OK' : 'зависимости'],
      ['Транскрипт', status.stage === 'transcription' ? `${status.current_batch || 0}/${status.total_batches || '?'}` : 'Whisper'],
      ['AI отбор', candidates.length ? `${candidates.length} канд.` : 'Ollama'],
      ['Visual/OCR', visualQuality?.recommendations ? 'отчёт' : settings.visual_scan_enabled ? 'включён' : 'выкл'],
      ['Рендер', finalReady ? 'готов' : segments.length ? `${segments.length} фрагм.` : 'ожидает'],
      ['Готово', finalReady ? 'файл есть' : '—'],
    ]
    const active = currentPhaseIndex()
    return <div className="phaseRail">{phases.map(([name, hint], idx) => <div key={name} className={`phaseItem ${idx < active ? 'done' : ''} ${idx === active ? 'active' : ''}`}>
      <span>{idx + 1}</span><b>{name}</b><small>{hint}</small>
    </div>)}</div>
  }







  async function createSupportBundle() {
    setSupportBundleBusy(true)
    try {
      const response = await apiFetch(`${API}/support-bundle`, { method: 'POST' })
      if (!response.ok) {
        const payload = await safeJsonResponse(response, null)
        throw new Error(errorFromPayload(payload, 'Не удалось создать отчёт поддержки'))
      }
      const blob = await response.blob()
      const disposition = response.headers.get('content-disposition') || ''
      const match = disposition.match(/filename="?([^";]+)"?/i)
      const filename = match?.[1] || `highlight-studio-support-${Date.now()}.zip`
      const href = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = href
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(href)
      setError('Отчёт для поддержки создан. Видео, транскрипты и секреты в него не входят.')
    } catch (error) {
      notifyError('Не удалось создать отчёт поддержки', error)
    } finally {
      setSupportBundleBusy(false)
    }
  }

  async function updateAction(action) {
    try {
      const result = action === 'check'
        ? await checkNativeUpdates()
        : action === 'download'
          ? await downloadNativeUpdate()
          : await installNativeUpdate()
      if (result) setUpdateState(result)
      if (result?.ok === false && result?.message) setError(result.message)
    } catch (error) {
      notifyError('Не удалось выполнить обновление', error)
    }
  }

  function renderUpdateCard() {
    if (!desktopInfo?.desktop) return null
    const status = updateState?.status || 'disabled'
    const action = status === 'available'
      ? <button onClick={() => updateAction('download')}>Скачать обновление</button>
      : status === 'downloaded'
        ? <button className="primaryStrong" onClick={() => updateAction('install')}>Установить и перезапустить</button>
        : <button onClick={() => updateAction('check')} disabled={!updateState?.supported || status === 'checking'}>{status === 'checking' ? 'Проверяю…' : 'Проверить обновления'}</button>
    return <section className="uCard updaterCard"><div className="uCardHead"><div><span>Версия приложения</span><h3>Обновления</h3><p>{updateState?.message || 'Проверка обновлений доступна в установленной desktop-версии.'}</p></div>{action}</div>{Number.isFinite(updateState?.percent) && status === 'downloading' && <div className="progress"><span style={{ width: `${updateState.percent}%` }}/></div>}</section>
  }

  function renderTopBar() {
    const taskState = String(status.state || '').toLowerCase()
    const activeGlobalJobs = globalJobs.filter(job => ['queued','running','cancel_requested'].includes(String(job.state || '').toLowerCase()))
    const taskLabel = sourceRequest || (busy
      ? `${Math.round(progressValue)}% · ${humanizePipelineStage(status.stage)}`
      : activeGlobalJobs.length
        ? `${activeGlobalJobs.length} ${activeGlobalJobs.length === 1 ? 'задача' : 'задачи'}`
        : taskState === 'error'
          ? 'Задача с ошибкой'
          : 'Задачи')
    const licenseNeedsAction = licenseStatus && !['active', 'trial'].includes(licenseStatus.state)
    const trialEndsSoon = licenseStatus?.state === 'trial' && Number(licenseStatus.days_remaining) <= 3
    const activeWorkflow = workflowSteps.find(step => step.id === activeStep)
    const sectionTitle = activeStep === 'projects' ? 'Проекты' : activeStep === 'reports' ? 'Диагностика' : activeStep === 'settings' ? 'Настройки' : activeWorkflow?.title || 'Рабочее пространство'
    return <header className="studioCommandbar">
      <button className="mobileMenuButton" aria-label="Открыть навигацию" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(value => !value)}><Menu size={19}/></button>
      <div className="commandBreadcrumb" aria-label="Текущий раздел">
        <span>{project?.name || 'Highlight Studio'}</span>
        <ChevronRight size={14}/>
        <b>{sectionTitle}</b>
      </div>
      <div className="commandbarContext">
        {project && <span className={`autosaveState ${pendingCandidateKeys.length ? 'saving' : ''}`} aria-live="polite"><i/>{pendingCandidateKeys.length ? 'Сохраняю монтаж…' : 'Все изменения сохранены'}</span>}
        <span className={`engineState ${backendOk === false ? 'error' : 'ready'}`}><i/>{backendOk === false ? 'Движок недоступен' : 'Движок готов'}</span>
      </div>
      <div className="quickActions commandbarActions">
        {backendOk === false && <button className="headerWarning" onClick={() => refreshProjects()}>Движок недоступен</button>}
        {(licenseNeedsAction || trialEndsSoon) && <button className="headerWarning" onClick={() => setActiveStep('settings')}>{licenseNeedsAction ? 'Нужна активация' : `Пробный период · ${licenseStatus.days_remaining} дн.`}</button>}
        <button className={`taskCenterButton ${busy ? 'running' : taskState === 'error' ? 'error' : ''}`} aria-expanded={taskCenterOpen} onClick={() => setTaskCenterOpen(value => !value)}>
          <Clock3 size={17}/><span>{taskLabel}</span>{busy && <i aria-hidden="true"/>}
        </button>
        {authStatus?.accounts_enabled && currentUser && <button className="accountPill" onClick={() => setActiveStep('settings')}><span>{(currentUser.display_name || currentUser.email || '?')[0].toUpperCase()}</span><b>{currentUser.display_name}</b></button>}
        <button className="iconButton" aria-label="Помощь и диагностика" title="Помощь и диагностика" onClick={() => { setActiveStep('reports'); setDiagnosticsExpanded(false) }}><CircleHelp size={18}/></button>
        <div className="utilityMenuWrap">
          <button ref={utilityButtonRef} className="iconButton" aria-label="Дополнительные действия" aria-haspopup="menu" aria-expanded={showUtilityMenu} onClick={() => setShowUtilityMenu(value => !value)}><MoreHorizontal size={20}/></button>
          {showUtilityMenu && <div ref={utilityMenuRef} className="utilityMenu" role="menu" aria-label="Дополнительные действия">
            <div className="utilityMenuSection" role="radiogroup" aria-label="Цветовая тема">
              <span className="utilityMenuLabel">Тема</span>
              {THEME_OPTIONS.map(theme => <button role="radio" key={theme.id} className={uiTheme === theme.id ? 'selected' : ''} aria-checked={uiTheme === theme.id} onClick={() => selectTheme(theme.id)}><Palette size={15}/>{theme.label}</button>)}
            </div>
            <div className="utilityMenuSection">
              <span className="utilityMenuLabel">Интерфейс</span>
              <button role="menuitem" onClick={() => {toggleDensity();setShowUtilityMenu(false)}}>{uiDensity === 'compact' ? 'Комфортный вид' : 'Компактный вид'}</button>
              <button role="menuitem" onClick={() => {setMode(productMode === 'stable' ? 'pro' : 'stable');setShowUtilityMenu(false)}}>{productMode === 'stable' ? 'Включить расширенный режим' : 'Вернуться в основной режим'}</button>
              <button role="menuitem" onClick={() => {setActiveStep('settings');setShowUtilityMenu(false)}}>Настройки</button>
            </div>
            <div className="utilityMenuSection">
              {(!authStatus?.accounts_enabled || currentUser?.global_role === 'admin') && <button role="menuitem" onClick={() => {runSystemCheck(true);setShowUtilityMenu(false)}}>Диагностика системы</button>}
              {(!authStatus?.accounts_enabled || currentUser?.global_role === 'admin') && <button role="menuitem" onClick={() => {setFirstRunOpen(true);setShowUtilityMenu(false)}}>Мастер первого запуска</button>}
              {(!authStatus?.accounts_enabled || currentUser?.global_role === 'admin') && <button role="menuitem" onClick={() => {createSupportBundle();setShowUtilityMenu(false)}} disabled={supportBundleBusy}>{supportBundleBusy ? 'Создаю отчёт…' : 'Отчёт для поддержки'}</button>}
              {project && !authStatus?.accounts_enabled && <button role="menuitem" onClick={() => {showProjectFolder();setShowUtilityMenu(false)}}>Открыть папку проекта</button>}
            </div>
            <div className="utilityVersion">Highlight Studio v11.2.7</div>
          </div>}
        </div>
      </div>
    </header>
  }


  function renderJobBar() {
    const stageProgress = Math.max(0, Math.min(100, Number(status.stage_progress_percent ?? status.batch_progress_percent) || 0))
    const hasStageProgress = status.progress_source === 'real_counter' || Number(status.total_batches) > 0 || Number(status.stage_progress_percent) > 0
    const taskState = String(status.state || '').toLowerCase()
    const hasTask = busy || ['error', 'done', 'cancelled', 'paused'].includes(taskState)
    const hasBackgroundJobs = globalJobs.some(job => ['queued','running','cancel_requested'].includes(String(job.state || '').toLowerCase()))
    if (!taskCenterOpen) return null
    return <section className={`jobBar taskCenter ${hasTask ? taskState || 'active' : 'idleCompact'}`} aria-live="polite" aria-label="Центр задач">
      <div className="taskCenterHeader">
        <div className={`jobDot ${taskState || 'ready'}`} aria-hidden="true"/>
        <div>
          <b>{sourceRequest || (hasTask ? (busy ? humanizePipelineStage(status.stage || status.message || 'Задача обновляется') : status.message || 'Задача обновляется') : hasBackgroundJobs ? 'Фоновые задачи' : 'Нет активных задач')}</b>
          <span>{sourceRequest ? 'Ожидаем ответ сервера' : hasBackgroundJobs ? 'Задачи выполняются. Не закрывай приложение до завершения.' : hasTask ? (busy ? 'Прогресс сохраняется в проекте' : taskState === 'done' ? 'Можно закрыть приложение' : 'Проверь состояние задачи') : 'Можно продолжать работу или закрыть приложение'}</span>
        </div>
        <div className="taskCenterHeaderActions">
          {busy && <button className="dangerBtn" onClick={() => runSimple('cancel')}>Остановить</button>}
          <button className="iconButton" aria-label="Закрыть центр задач" onClick={() => setTaskCenterOpen(false)}><X size={17}/></button>
        </div>
      </div>
      {hasTask && <div className="jobMain">
        <div className="jobLine"><b>{humanizePipelineStage(status.stage || 'Общий прогресс')}</b><span>{Math.round(progressValue)}%</span></div>
        <div className="progress" role="progressbar" aria-label="Общий прогресс задачи" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(progressValue)}><span style={{ width: `${progressValue}%` }} /></div>
        <div className="progressCaption">
          <span>{progressKindLabel(status.progress_source)}</span>
          {status.eta_seconds ? <span>Осталось: {formatDuration(status.eta_seconds)}</span> : null}
          {status.elapsed_seconds ? <span>Прошло: {formatDuration(status.elapsed_seconds)}</span> : null}
          {status.estimated_finish_at ? <span>Готово примерно в {new Date(status.estimated_finish_at * 1000).toLocaleTimeString()}</span> : null}
          <button className="inlineMini" onClick={() => setProgressExpanded(value => !value)}>{progressExpanded ? 'Скрыть детали' : 'Показать детали'}</button>
        </div>
        {progressExpanded && renderPhaseRail()}
        {progressExpanded && renderTaskDetails()}
        {progressExpanded && hasStageProgress && <div className="stageProgressBox">
          <div className="stageHeader">
            <b>{humanizePipelineStage(status.stage || 'Текущий этап')}</b>
            <span>{stageProgress.toFixed(stageProgress % 1 ? 1 : 0)}%</span>
          </div>
          <div className="progress stage" role="progressbar" aria-label="Прогресс текущего этапа" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(stageProgress)}><span style={{ width: `${stageProgress}%` }} /></div>
          <div className="jobMeta">
            {status.total_batches ? <span>Счётчик: {status.current_batch}/{status.total_batches}</span> : <span>Счётчик —</span>}
            {status.remaining_items !== undefined ? <span>Осталось элементов: {status.remaining_items}</span> : null}
            {status.items_per_minute ? <span>Скорость: {status.items_per_minute}/мин</span> : null}
            {status.stage_eta_seconds ? <span>Осталось на этапе: {formatDuration(status.stage_eta_seconds)}</span> : null}
          </div>
        </div>}
        <div className="taskCenterFooter"><span>{project?.name || 'Текущий проект'}</span><button onClick={() => { refreshGlobalJobs(); if (project) refreshAll() }}><RefreshCw size={14}/> Обновить</button></div>
      </div>}
      <div className="globalTaskList">
        <div className="globalTaskListHead"><b>Все фоновые задачи</b><span>{globalJobs.filter(job => ['queued','running','cancel_requested'].includes(String(job.state || '').toLowerCase())).length} активных</span></div>
        {globalJobsError && <p className="taskCenterError" role="status">{globalJobsError} · Показаны последние известные данные.</p>}
        {globalJobs.length ? globalJobs.slice(0, 8).map(job => {
          const owner = projects.find(item => item.id === job.project_id)
          const currentActive = busy && job.project_id === project?.id && ['queued','running','cancel_requested'].includes(String(job.state || '').toLowerCase())
          const pct = currentActive ? progressValue : Math.max(0, Math.min(100, Number(job.progress || 0)))
          return <button key={job.id} className={`globalTaskRow ${String(job.state || '').toLowerCase()}`} onClick={() => { if (job.project_id) { setTaskCenterOpen(false); loadProjectById(job.project_id) } }}>
            <span className="globalTaskCopy"><b>{owner?.name || job.project_id || 'Проект'}</b><small>{humanizePipelineStage(job.kind || job.title)} · {jobStateLabel(job.state)}</small></span>
            <span className="globalTaskProgress"><i style={{width:`${pct}%`}}/><small>{Math.round(pct)}%</small></span>
          </button>
        }) : <p className="muted">Фоновых задач пока нет.</p>}
      </div>
    </section>
  }










  function renderProductSidebar() {
    const completedSteps = workflowSteps.filter(step => step.completed).length
    const projectProgress = Math.round((completedSteps / workflowSteps.length) * 100)
    return <>
      {mobileNavOpen && <button className="sidebarScrim" aria-label="Закрыть навигацию" onClick={() => setMobileNavOpen(false)}/>} 
      <aside id="studio-product-sidebar" className={`studioSidebar ${sidebarCompact ? 'compact' : ''} ${mobileNavOpen ? 'mobileOpen' : ''}`} aria-label="Навигация Highlight Studio">
        <div className="sidebarBrandRow">
          <button className="sidebarBrand" onClick={() => { setActiveStep('projects'); setMobileNavOpen(false) }} aria-label="Highlight Studio — проекты">
            <span className="sidebarBrandMark"><Scissors size={20}/></span>
            <span className="sidebarBrandText"><b>Highlight</b><small>STUDIO</small></span>
          </button>
          <button
            className="sidebarCollapse"
            aria-controls="studio-product-sidebar"
            aria-expanded={!sidebarCompact}
            aria-label={sidebarCompact ? 'Развернуть меню' : 'Свернуть меню'}
            title={sidebarCompact ? 'Развернуть меню' : 'Свернуть меню'}
            onClick={toggleProductSidebar}
          >{sidebarCompact ? <PanelLeftOpen size={17}/> : <PanelLeftClose size={17}/>}</button>
          <button className="sidebarMobileClose" aria-label="Закрыть меню" onClick={() => setMobileNavOpen(false)}><X size={18}/></button>
        </div>

        <button className="projectSwitcher topProjectIdentity" onClick={() => { setActiveStep('projects'); setMobileNavOpen(false) }} title={project?.name || 'Выбрать проект'}>
          <span className="projectSwitcherIcon"><FolderOpen size={17}/></span>
          <span className="projectSwitcherCopy"><small>Текущий проект</small><b>{project?.name || 'Выбрать проект'}</b></span>
          <ChevronDown size={14}/>
        </button>

        <button className="newProjectButton" title="Новый проект" onClick={() => { beginProject('local', false); setMobileNavOpen(false) }}><Upload size={17}/><span>Новый проект</span></button>

        <div className="sidebarNavSection">
          <span className="sidebarSectionLabel">Рабочая область</span>
          <button title="Обзор проектов" className={`sidebarNavItem ${activeStep === 'projects' ? 'active' : ''}`} onClick={() => { setActiveStep('projects'); setMobileNavOpen(false) }} aria-current={activeStep === 'projects' ? 'page' : undefined}>
            <Home size={18}/><span>Обзор проектов</span>
          </button>
        </div>

        <div className="sidebarNavSection workflowNavSection">
          <div className="sidebarSectionHeading"><span className="sidebarSectionLabel">Создание ролика</span>{project && <small>{projectProgress}%</small>}</div>
          {project && <div className="sidebarProjectProgress" aria-label={`Проект готов на ${projectProgress}%`}><i style={{ width: `${projectProgress}%` }}/></div>}
          <ol className="sidebarWorkflowList">
            {workflowSteps.map((step, index) => {
              const state = activeStep === step.id ? 'current' : step.completed ? 'completed' : step.locked ? 'blocked' : 'available'
              return <li key={step.id} className={state}>
                <button aria-current={activeStep === step.id ? 'step' : undefined} data-locked={step.locked ? 'true' : 'false'} className={`sidebarNavItem workflowNavItem ${state}`} onClick={() => openWorkflowStep(step)} title={step.locked ? step.blockedReason : `${step.title}: ${step.hint}`}>
                  <span className="workflowNavIcon">{step.completed && activeStep !== step.id ? <Check size={16}/> : step.icon}</span>
                  <span className="workflowNavCopy"><b>{step.title}</b><small>{step.hint}</small></span>
                  <span className="workflowNavIndex">0{index + 1}</span>
                </button>
              </li>
            })}
          </ol>
        </div>

        {project && <div className="sidebarProjectMini" aria-label="Сводка проекта">
          <div><span>Найдено</span><b>{candidates.length}</b></div>
          <div><span>Выбрано</span><b>{segments.length}</b></div>
          <div className="wide"><span>Длительность</span><b>{formatDuration(totalFinalDuration)}</b></div>
        </div>}

        <div className="sidebarBottomNav">
          <button title="Диагностика" className={`sidebarNavItem ${activeStep === 'reports' ? 'active' : ''}`} onClick={() => { setActiveStep('reports'); setDiagnosticsExpanded(false); setMobileNavOpen(false) }}><CircleHelp size={18}/><span>Диагностика</span></button>
          <button title="Настройки" className={`sidebarNavItem ${activeStep === 'settings' ? 'active' : ''}`} onClick={() => { setActiveStep('settings'); setMobileNavOpen(false) }}><Settings size={18}/><span>Настройки</span></button>
        </div>

        <div className="sidebarFooter"><span className="sidebarAvatar">HS</span><span><b>Highlight Studio</b><small>Версия 11.2.7</small></span><LayoutGrid size={16}/></div>
      </aside>
    </>
  }

  function resetProjectContextForDraft() {
    // "New project" is a real draft context, not a Source screen rendered on
    // top of the previous project.  Invalidate all project-scoped async work so
    // late responses from A cannot repopulate the draft or the next project B.
    beginProjectScope('')
    setProject(null)
    setProjectAccess(null)
    setCandidates([])
    setSegments([])
    setSegmentsRevision('')
    setStatus({})
    setStatusHistory([])
    setLogs('')
    setOutputs([])
    setPreRenderReport(null)
    setVisualQuality(null)
    setAutoRec(null)
    setSmartPreflight(null)
    setCheckpoints(null)
    setDurationControl(null)
    setRenderQuality(null)
    setCreatorPack(null)
    setProjectHistory([])
    setIntegrity(null)
    setAiQualityAudit(null)
    setWorkflowGuard(null)
    setRenderArtifactCheck(null)
    setAppAudit(null)
    setQualityLock(null)
    setFinalSuccess(null)
    setSuccessHistory([])
    setStableCandidate(null)
    setPreviewClip(null)
    setClipPreview({ url: '', loading: false, error: '', key: '' })
    setExpandedPreviewKey(null)
    setRejectedCandidateKeys([])
    setRejectedLoadedProjectId(null)
    setSegmentUndoStack([])
    setSegmentRedoStack([])
    setFormatConfirmed(false)
    setYoutubeReport(null)
    setSelectedFile(null)
    setFastImportPath('')
    setTwitchUrl('')
    setTwitchStart('')
    setTwitchEnd('')
    setLocalBrowserOpen(false)
    setLocalBrowseError('')
    setNoticeState(null)
    try { localStorage.removeItem('highlightStudioLastProject') } catch (_) {}
  }

  function beginProject(source = 'local', openPicker = false) {
    resetProjectContextForDraft()
    setSourceChoice(source)
    setTwitchKind(source === 'twitch_live' ? 'live' : 'vod')
    setActiveStep('import')
    if (source === 'local' && openPicker) {
      openLocalBrowser()
    } else if (source !== 'local') {
      requestAnimationFrame(() => sourcePathInputRef.current?.focus())
    }
  }

  function renderProjectsHome() {
    let recoveredTask = null
    try {
      const saved = JSON.parse(localStorage.getItem('highlightStudioActiveTask') || 'null')
      if (saved && Date.now() - Number(saved.savedAt || 0) < 24 * 60 * 60 * 1000) recoveredTask = saved
    } catch (_) {
      recoveredTask = null
    }
    const nextStep = workflowSteps.find(step => !step.completed && !step.locked) || workflowSteps.at(-1)
    return <div className="uPage projectsHome dashboardPage">
      <header className="dashboardPageHeader">
        <div><span className="dashboardEyebrow">Твоя монтажная</span><h1>{project ? 'Продолжим с того места, где остановились' : 'Преврати длинное видео в сильную нарезку'}</h1><p>Импорт, AI-анализ, ручная проверка и публикация — в одном понятном рабочем процессе.</p></div>
        <div className="dashboardHeaderActions"><button onClick={refreshProjects}><RefreshCw size={16}/> Обновить</button><button className="primaryStrong" onClick={() => beginProject('local', false)}><Upload size={17}/> Новый проект</button></div>
      </header>

      {recoveredTask && <section className="resumeTaskCard dashboardResume">
        <div className="resumeTaskIcon"><Clock3 size={21}/></div>
        <div><span>Незавершённая задача</span><h3>{recoveredTask.projectName || 'Проект'}</h3><p>{recoveredTask.message || recoveredTask.stage || 'Состояние задачи сохранено'} · {Math.round(Number(recoveredTask.progress || 0))}%</p></div>
        <div className="resumeMiniProgress"><i style={{width: `${Math.round(Number(recoveredTask.progress || 0))}%`}}/></div>
        <button onClick={() => recoveredTask.projectId ? loadProjectById(recoveredTask.projectId) : setTaskCenterOpen(true)}>Продолжить <ChevronRight size={15}/></button>
      </section>}

      <div className="dashboardOverviewGrid">
        <section className={`projectSpotlight ${project ? 'hasProject' : 'empty'}`}>
          <div className="projectSpotlightGlow" aria-hidden="true"/>
          {project ? <>
            <div className="projectSpotlightHead"><div><span className="sectionKicker">Активный проект</span><h2>{project.name}</h2><p>{project.source_type === 'twitch' ? 'Twitch' : 'Локальное видео'} · {sourceReady ? 'источник готов' : 'нужно подготовить источник'}</p></div><span className={`projectStatusBadge ${busy ? 'running' : 'saved'}`}><i/>{busy ? 'Обрабатывается' : 'Сохранён'}</span></div>
            <div className="projectWorkflowPreview">{workflowSteps.map((step, index) => <button key={step.id} className={`${activeStep === step.id ? 'active' : ''} ${step.completed ? 'done' : ''} ${step.locked ? 'locked' : ''}`} onClick={() => openWorkflowStep(step)}><span>{step.completed ? <Check size={14}/> : index + 1}</span><b>{step.title}</b></button>)}</div>
            <div className="projectSpotlightStats">
              <div><small>Найдено AI</small><b>{candidates.length}</b><span>моментов</span></div>
              <div><small>AI-нарезка</small><b>{segments.length}</b><span>фрагментов · можно проверить</span></div>
              <div><small>Итог</small><b>{formatDuration(totalFinalDuration)}</b><span>длительность</span></div>
              <div><small>Профиль</small><b className="textMetric">{settings.task_preset_label || 'Баланс'}</b><span>{settings.target_minutes || 30} мин ориентир</span></div>
            </div>
            <div className="projectSpotlightActions"><button onClick={showProjectFolder}><FolderOpen size={16}/> Папка проекта</button><button className="primaryStrong" onClick={() => openWorkflowStep(nextStep)}><Play size={16}/> Продолжить: {nextStep.title}</button></div>
          </> : <div className="spotlightEmptyState"><span className="spotlightEmptyIcon"><Film size={28}/></span><span className="sectionKicker">Первый проект</span><h2>Начни с исходного видео</h2><p>Приложение проведёт через пять коротких шагов и сохранит прогресс после каждого действия.</p><button className="primaryStrong" onClick={() => beginProject('local', false)}><Upload size={17}/> Выбрать источник</button></div>}
        </section>

        <aside className="newProjectPanel">
          <div className="newProjectPanelHead"><span className="sectionKicker">Быстрый старт</span><h2>Новый проект</h2><p>Выбери, откуда взять исходник.</p></div>
          <div className="sourceLaunchGrid">
            <button className="sourceLaunch primarySource" onClick={() => beginProject('local', true)}><span className="sourceLaunchIcon"><HardDrive size={19}/></span><span><b>Файл с компьютера</b><small>Использовать видео на месте</small></span><ChevronRight size={15}/></button>
            <button className="sourceLaunch" onClick={() => beginProject('twitch_vod')}><span className="sourceLaunchIcon twitch"><Play size={18}/></span><span><b>Twitch VOD</b><small>Запись или диапазон</small></span><ChevronRight size={15}/></button>
            <button className="sourceLaunch" onClick={() => beginProject('twitch_live')}><span className="sourceLaunchIcon live"><Radio size={18}/></span><span><b>Twitch Live</b><small>Записать текущий эфир</small></span><ChevronRight size={15}/></button>
          </div>
          <div className="localPrivacyNote"><ShieldCheck size={17}/><span><b>Локальная обработка</b><small>Исходники не отправляются в облако</small></span></div>
        </aside>
      </div>

      <section className="recentProjectsSection dashboardRecent">
        <div className="sectionHeading"><div><span className="sectionKicker">Библиотека</span><h2>Недавние проекты</h2></div><span className="projectCount">{projects.length} всего</span></div>
        {projects.length ? <div className="recentProjectGrid">{projects.slice(0, 8).map(item => <button className={`recentProjectRow ${project?.id === item.id ? 'active' : ''}`} key={item.id} onClick={() => loadProjectById(item.id)}>
          <span className="recentProjectThumb"><Film size={19}/><i/></span>
          <span className="recentProjectCopy"><b>{item.name || item.id}</b><small>{item.source_type === 'twitch' ? 'Twitch' : 'Локальное видео'} · {item.status || 'сохранён'}</small></span>
          <span className="recentProjectState">{project?.id === item.id ? 'Открыт' : 'Открыть'}</span>
          <ChevronRight className="rowArrow" size={16}/>
        </button>)}</div> : <div className="emptyProjectState"><Film size={24}/><b>Проектов пока нет</b><span>Открой первое видео — настройка займёт меньше минуты.</span></div>}
      </section>

      <div className="dashboardSystemLine">{renderUnifiedSystemPanel()}</div>
    </div>
  }



  function renderLocalBrowser() {
    return <div className="localBrowser redone">
      <div className="localBrowserTop"><b>Обзор диска</b><button onClick={() => setLocalBrowserOpen(false)}>Закрыть</button></div>
      {localBrowseError && <div className="miniError">{localBrowseError}</div>}
      <div className="rootsRow">{(localBrowser.roots || []).map(root => <button key={root.path} onClick={() => browseLocal(root.path)}>{root.name}</button>)}</div>
      <div className="pathRow">{localBrowser.parent && <button onClick={() => browseLocal(localBrowser.parent)}>← Вверх</button>}<span>{localBrowser.path || 'Домашняя папка'}</span></div>
      <div className="fileList">
        {(localBrowser.dirs || []).map(dir => <button key={dir.path} className="folderItem" onClick={() => browseLocal(dir.path)}>📁 {dir.name}</button>)}
        {(localBrowser.videos || []).map(video => <button key={video.path} className="videoItem" onClick={() => chooseLocalVideo(video)}>🎬 <span>{video.name}</span><small>{video.size_gb ? `${video.size_gb} GB` : `${video.size_mb} MB`}</small></button>)}
        {!(localBrowser.dirs || []).length && !(localBrowser.videos || []).length && <small>В этой папке не найдено видео mp4/mkv/mov/webm/avi/ts.</small>}
      </div>
    </div>
  }

  function renderUnifiedSystemPanel() {
    const c = systemCheck?.checks || {}
    const required = [c.ffmpeg?.ok && c.ffprobe?.ok, c.ollama_server?.ok, c.ollama_server?.text_model_ok]
    const readyCount = required.filter(Boolean).length
    if (!systemCheck) return <section className="systemReadyLine checking"><Clock3 size={18}/><div><b>Проверяем готовность приложения</b><span>Это займёт несколько секунд.</span></div></section>
    if (readyCount === required.length && productMode === 'stable') {
      return <section className="systemReadyLine"><Check size={18}/><div><b>Система готова</b><span>{systemCheck?.hardware?.summary || 'Видеообработка и локальный AI доступны.'}</span></div><button onClick={() => setActiveStep('reports')}>Подробнее</button></section>
    }
    return <section className="uCard uSystemPanel">
      <div className="uCardHead"><div><span>Перед запуском</span><h3>{readyCount === required.length ? 'Приложение готово' : 'Нужно проверить компоненты'}</h3><p>{readyCount === required.length ? 'Видео, локальный AI и модель доступны.' : 'Автоматическая проверка покажет, что именно нужно установить или запустить.'}</p></div><button onClick={() => runSystemCheck(true)} disabled={systemCheckLoading}>{systemCheckLoading ? 'Проверяю…' : 'Проверить'}</button></div>
      <div className="uStatusGrid simple"><div className={c.ffmpeg?.ok && c.ffprobe?.ok ? 'ok':'bad'}><b>Обработка видео</b><span>{c.ffmpeg?.ok && c.ffprobe?.ok ? 'Готово':'Требуется FFmpeg'}</span></div><div className={c.ollama_server?.ok ? 'ok':'bad'}><b>Локальный AI</b><span>{c.ollama_server?.ok ? 'Готово':'Запусти Ollama'}</span></div><div className={c.ollama_server?.text_model_ok ? 'ok':'bad'}><b>AI-модель</b><span>{c.ollama_server?.text_model_ok ? 'Готово':'Нужно скачать модель'}</span></div></div>
      {desktopInfo?.desktop && <div className="desktopRuntimeLine"><b>Desktop-режим</b><span>{desktopInfo.packaged ? 'Установленная версия' : 'Режим разработки'} · проекты: {desktopInfo.projectsDir}</span></div>}
      {!!systemCheck?.recommendations?.length && <div className="uAdvice"><b>Что сделать:</b>{systemCheck.recommendations.slice(0,2).map((r,i)=><p key={i}>• {r}</p>)}</div>}
      {productMode === 'pro' && <button className="textButton" onClick={() => setDiagnosticsExpanded(v=>!v)}>{diagnosticsExpanded ? 'Скрыть технические детали' : 'Показать технические детали'}</button>}
      {productMode === 'pro' && diagnosticsExpanded && <div className="technicalDetails">Whisper: {settings.whisper_model}/{settings.whisper_device}/{settings.whisper_compute} · GPU: {c.ctranslate2_cuda?.ok ? 'CUDA ✓' : 'CPU fallback'} · NVENC: {c.ffmpeg_nvenc?.ok ? '✓' : 'нет'} · Vision: {settings.visual_scan_enabled ? settings.vision_model : 'выключен'} · Twitch tools: {c.yt_dlp?.ok ? 'готовы' : 'не проверены'}</div>}
    </section>
  }


  function renderUnifiedProjectPanel() {
    return <section className="uCard">
      <div className="uCardHead"><div><span>Проект</span><h3>{project ? project.name : 'Проект ещё не создан'}</h3><p>{project ? `${project.source_type || 'file'} · ${project.storage_mode || 'copy'}${project.source_video_size_gb ? ` · ${project.source_video_size_gb} GB` : ''}` : 'После выбора источника здесь появится карточка проекта.'}</p></div><button onClick={refreshProjects}>Обновить</button></div>
      {project ? <div className="uProjectStats">
        <div><b>{sourceReady ? 'Готов' : 'Не готов'}</b><span>источник</span></div>
        <div><b>{candidates.length}</b><span>AI кандидатов</span></div>
        <div><b>{segments.length}</b><span>фрагментов</span></div>
        <div><b>{formatDuration(totalFinalDuration)}</b><span>длительность</span></div>
      </div> : <p className="muted">Выбери источник: локальный файл, Twitch VOD или Twitch Live.</p>}
      <div className="uCompactActions">
        <select aria-label="Открыть недавний проект" value={project?.id || ''} onChange={e => { if (e.target.value) loadProjectById(e.target.value) }}>
          <option value="">Последние проекты</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {project && <><button onClick={showProjectFolder}>Папка</button><button onClick={exportProject}>ZIP</button><button onClick={clearCache}>Кэш</button>{project.source_type === 'twitch' && !sourceReady && <button className="primaryStrong" onClick={prepareTwitchSource}>Подготовить источник</button>}</>}
      </div>
    </section>
  }

  function renderUnifiedImportStep() {
    const advancedVisible = advancedToolsOpen ?? (productMode === 'pro')
    return <div className="uPage">
      <StepHeader eyebrow="Шаг 1 из 5" title="Добавь исходное видео" description="Начни с файла на компьютере, записи Twitch или текущего эфира." />
      <div className="uSourceTabs compactTabs" role="tablist" aria-label="Тип источника">
        <button role="tab" aria-selected={sourceChoice === 'local'} className={sourceChoice === 'local' ? 'active' : ''} onClick={() => {setSourceChoice('local');setTwitchKind('vod')}}><Film size={19}/><span><b>Файл</b><small>С компьютера</small></span></button>
        <button role="tab" aria-selected={sourceChoice === 'twitch_vod'} className={sourceChoice === 'twitch_vod' ? 'active' : ''} onClick={() => {setSourceChoice('twitch_vod');setTwitchKind('vod')}}><Download size={19}/><span><b>Twitch VOD</b><small>Запись по ссылке</small></span></button>
        <button role="tab" aria-selected={sourceChoice === 'twitch_live'} className={sourceChoice === 'twitch_live' ? 'active' : ''} onClick={() => {setSourceChoice('twitch_live');setTwitchKind('live')}}><Zap size={19}/><span><b>Twitch Live</b><small>Записать эфир</small></span></button>
      </div>
      <div className="uTwoColumns sourceLayout">
        <section className="uCard uSourceForm">
          <div className="uCardHead"><div><span>Источник</span><h3>{sourceChoice === 'local' ? 'Видео с компьютера' : sourceChoice === 'twitch_vod' ? 'Запись Twitch' : 'Текущий эфир Twitch'}</h3><p>{sourceChoice === 'local' ? 'Файл останется на месте — тяжёлая копия не создаётся.' : 'Вставь ссылку и укажи только нужные параметры.'}</p></div></div>
          {sourceChoice === 'local' ? <>
            <button className={`sourceDropzone ${fastImportPath ? 'selected' : ''}`} onClick={openLocalBrowser}>
              <span className="sourceDropIcon"><Upload size={22}/></span>
              <span><b>{fastImportPath ? 'Видео выбрано' : 'Открыть видео'}</b><small>{fastImportPath || 'MP4, MKV, MOV, WEBM, AVI или TS'}</small></span>
            </button>
            <label className="uField"><span>Путь к видео</span><input ref={sourcePathInputRef} placeholder="D:\Streams\stream.mp4" value={fastImportPath} onChange={event => setFastImportPath(event.target.value)}/></label>
            <div className="primaryActionRow">
              <button className={!fastImportPath ? 'primaryStrong' : ''} onClick={openLocalBrowser}>Выбрать другой файл</button>
              <button className={fastImportPath ? 'primaryStrong' : ''} onClick={fastImport} disabled={!fastImportPath.trim() || Boolean(sourceRequest)}>{sourceRequest || 'Создать проект'}</button>
            </div>
            <button className="textButton" aria-expanded={advancedVisible} onClick={() => setAdvancedToolsOpen(!advancedVisible)}>{advancedVisible ? 'Скрыть дополнительные способы' : 'Дополнительные способы импорта'}</button>
            {advancedVisible && <div className="uAdvancedBox">
              <div className="uSegmented" role="radiogroup" aria-label="Способ хранения исходника">
                <button role="radio" aria-checked={fastImportMode === 'reference'} className={fastImportMode === 'reference' ? 'selected' : ''} onClick={() => setFastImportMode('reference')}>Использовать на месте</button>
                <button role="radio" aria-checked={fastImportMode === 'link'} className={fastImportMode === 'link' ? 'selected' : ''} onClick={() => setFastImportMode('link')}>Создать ссылку</button>
                <button role="radio" aria-checked={fastImportMode === 'copy'} className={fastImportMode === 'copy' ? 'selected' : ''} onClick={() => setFastImportMode('copy')}>Скопировать в проект</button>
              </div>
              <p>Для больших видео рекомендуется «Использовать на месте».</p>
              <label className="nativeFileField"><span>Обычная загрузка до 2 ГБ</span><input type="file" accept="video/*" disabled={Boolean(sourceRequest)} onChange={upload}/></label>
              {selectedFile && <button onClick={() => uploadFile(selectedFile)} disabled={selectedFile.size > 2 * 1024 * 1024 * 1024 || Boolean(sourceRequest)}>Загрузить выбранный файл</button>}
            </div>}
            {localBrowserOpen && renderLocalBrowser()}
          </> : <>
            <label className="uField"><span>Ссылка Twitch</span><input ref={sourcePathInputRef} placeholder="https://www.twitch.tv/videos/1234567890" value={twitchUrl} onChange={event => setTwitchUrl(event.target.value)}/></label>
            {twitchKind === 'vod' ? <div className="uFormGrid">
              <label><span>Начало, необязательно</span><input placeholder="00:10:00" value={twitchStart} onChange={event => setTwitchStart(event.target.value)}/></label>
              <label><span>Конец, необязательно</span><input placeholder="01:20:00" value={twitchEnd} onChange={event => setTwitchEnd(event.target.value)}/></label>
              <label className="wide"><span>Качество</span><select value={twitchQuality} onChange={event => setTwitchQuality(event.target.value)}><option value="best">Лучшее доступное</option><option value="1080p60">1080p 60 FPS</option><option value="720p60">720p 60 FPS</option></select></label>
            </div> : <label className="uField"><span>Продолжительность записи, минут</span><input type="number" min="1" max="720" value={twitchLiveMinutes} onChange={event => setTwitchLiveMinutes(event.target.value)}/></label>}
            <button className="primaryStrong wideButton" onClick={() => twitchImport(true)} disabled={!twitchUrl.trim() || busy || Boolean(sourceRequest) || twitchSpeedTesting}>{sourceRequest || (busy && project?.source_type === 'twitch' ? 'Дождись завершения задачи' : 'Создать проект и подготовить видео')}</button>
            {project?.source_type === 'twitch' && !busy && <div className="sourceReadyActions"><p>{sourceReady ? 'Исходное видео готово.' : 'Проект создан. Подготовь источник перед анализом.'}</p>{!sourceReady ? <button onClick={prepareTwitchSource} disabled={Boolean(sourceRequest)}>Подготовить источник</button> : <button className="primaryStrong" onClick={() => setActiveStep('style')}>Перейти к формату</button>}</div>}
            {busy && renderTaskActivity()}
            <button className="textButton" aria-expanded={advancedVisible} onClick={() => setAdvancedToolsOpen(!advancedVisible)}>{advancedVisible ? 'Скрыть дополнительные параметры' : 'Дополнительные параметры Twitch'}</button>
            {advancedVisible && <div className="uAdvancedBox">
              <div className="uFormGrid">
                <label><span>Движок загрузки</span><select value={twitchEngine} onChange={event => setTwitchEngine(event.target.value)}><option value="auto">Auto Turbo — рекомендуется</option><option value="twitchdownloadercli">TwitchDownloaderCLI</option><option value="yt-dlp-aria2c">yt-dlp + aria2</option><option value="yt-dlp">yt-dlp</option></select></label>
                <label><span>Потоки</span><input type="number" min="1" max="64" value={twitchThreads} onChange={event => setTwitchThreads(event.target.value)}/></label>
                <label><span>Вход через браузер</span><select value={twitchCookiesBrowser} onChange={event => setTwitchCookiesBrowser(event.target.value)}><option value="none">Без входа</option><option value="firefox">Firefox</option><option value="chrome">Chrome</option><option value="edge">Edge</option></select></label>
                <label><span>Соединения aria2</span><input type="number" min="1" max="64" value={twitchAria2Connections} onChange={event => setTwitchAria2Connections(event.target.value)}/></label>
              </div>
              <p className="turboHint"><b>Auto Turbo</b> сначала использует встроенный TwitchDownloaderCLI, затем только при ошибке переключается на aria2/yt-dlp. Потоки по умолчанию: 16 — как в быстрых прошлых сборках.</p>
              <div className="buttonRow"><button onClick={runTwitchSpeedTest} disabled={twitchSpeedTesting || busy || Boolean(sourceRequest)}>{twitchSpeedTesting ? 'Проверяю…' : 'Проверить скорость'}</button><button onClick={twitchTest10Minutes} disabled={busy || Boolean(sourceRequest) || twitchSpeedTesting}>Тест 10 минут</button><button onClick={() => twitchImport(false)} disabled={Boolean(sourceRequest) || twitchSpeedTesting}>Создать без загрузки</button></div>
              {(busy || twitchSpeedTesting) && <p className="taskActionHint">Проверка скорости и тестовая загрузка доступны после завершения текущей операции. Остановить фоновую задачу можно в центре задач справа сверху. Новые параметры применятся к следующей загрузке.</p>}
            </div>}
          </>}
        </section>
        <aside className="sourceContext">{project ? renderUnifiedProjectPanel() : null}{renderUnifiedSystemPanel()}</aside>
      </div>
    </div>
  }


  function renderUnifiedStyleStep() {
    return <div className="uPage formatPage"><StepHeader eyebrow="Шаг 2 из 5" title="Какой результат тебе нужен" description="Выбери цель — длительность и производительность можно уточнить ниже." />
      <section className="uPresetGrid" aria-label="Формат результата">{TASK_PRESET_LIST.map((p, index) => <button key={p.id} aria-pressed={settings.task_preset === p.id} className={`uPreset ${settings.task_preset === p.id ? 'active' : ''}`} onClick={() => applyTaskPreset(p.id)}>
        <span className="presetCardTop"><span className="presetBadge">{p.badge}</span>{settings.task_preset === p.id && <i><Check size={13}/> Выбрано</i>}</span>
        <span className={`presetVisual presetVisual${index + 1}`} aria-hidden="true"><i/><i/><i/><i/><i/></span>
        <b>{p.label}</b><small>{p.desc}</small>
      </button>)}</section>
      <section className="uCard styleSettingsCard">
        <div className="uCardHead"><div><span>Параметры результата</span><h3>{settings.task_preset_label || 'Сбалансированный формат'}</h3><p>Для первого проекта оставь рекомендуемые значения.</p></div></div>
        <div className="uFormGrid styleFormGrid">
          <label><span>Тип контента</span><select value={settings.content_type || 'Auto'} onChange={event => setContentTypeSynced(event.target.value)}><option value="Auto">Автоматически</option><option>IRL стрим</option><option>IRL прогулка/город</option><option>Игры</option><option>Спорт</option><option>Подкаст</option><option>Shorts</option></select></label>
          <label><span>Стиль монтажа</span><select value={settings.edit_mode || 'Сбалансированный'} onChange={event => setEditModeSynced(event.target.value)}><option>Сбалансированный</option><option>Плотно</option><option>С историей</option><option value="Только смешное">Юмористические моменты</option><option value="Только конфликт/реакции">Конфликты и реакции</option><option value="IRL конфликт/хаос">Динамичный IRL</option></select></label>
          <label><span>Ориентир по длительности, минут</span><input type="number" min="1" max="240" value={settings.target_minutes || 30} onChange={event => setSettings({...settings, target_minutes: Number(event.target.value)})}/><small>Если сильных моментов меньше, ролик будет короче — слабые фрагменты не добавляются только ради точной длительности.</small></label>
          <label><span>Приоритет обработки</span><select value={settings.analysis_profile || 'balanced'} onChange={event => setSettings({...settings, analysis_profile: event.target.value})}><option value="fast">Быстрее</option><option value="balanced">Баланс — рекомендуется</option><option value="quality">Лучшее качество</option></select></label>
        </div>
        {autoRec && <div className="uRecommendation"><b>{autoRec.recommended_preset || autoRec.preset_label || 'Настройки подобраны'}</b><p>{autoRec.summary || autoRec.message}</p></div>}
        <div className="pageActionBar"><div><b>{settings.task_preset_label || 'Сбалансированный'}</b><span>Ориентир: {settings.target_minutes || 30} мин · {settings.analysis_profile === 'quality' ? 'максимум качества' : settings.analysis_profile === 'fast' ? 'быстрый анализ' : 'баланс скорости и качества'}</span></div><div><button onClick={applySmartAutopilot} disabled={!project}>Подобрать автоматически</button><button className="primaryStrong" onClick={continueToAnalysis} disabled={!project || !sourceReady}>Продолжить к анализу</button></div></div>
      </section>
      {productMode === 'pro' && <section className="uCard advancedSection"><div className="uCardHead"><div><span>Расширенный режим</span><h3>Профиль производительности</h3><p>Меняй только при нехватке скорости или памяти.</p></div></div><div className="uThreeActions"><button onClick={() => applyHardwarePreset('auto_fast')}>Быстрее</button><button onClick={() => applyHardwarePreset('auto_balanced')}>Баланс</button><button onClick={() => applyHardwarePreset('auto_quality')}>Качество</button></div></section>}
    </div>
  }


  function renderUnifiedAnalysisStep() {
    const analysisDone = analysisCurrent && !busy
    const pipelinePosition = currentPhaseIndex()
    return <div className="uPage analysisPage"><StepHeader eyebrow="Шаг 3 из 5" title={busy ? 'Анализируем видео' : analysisDone ? 'Моменты готовы' : 'Всё готово к анализу'} description={busy ? 'Можно свернуть центр задач и продолжить работу в приложении.' : analysisDone ? 'Просмотри результаты и оставь только сильные фрагменты.' : 'Проверим источник, расшифруем речь и найдём сильные моменты.'} />
      <section className="analysisPipelineMap" aria-label="Этапы анализа">
        {[['Подготовка','Исходник и зависимости'],['Транскрипция','Речь и таймкоды'],['AI-отбор','События и контекст'],['Visual','Кадр, чат и OCR'],['Результат','Готовые моменты']].map(([title, hint], index) => {
          const done = analysisDone || pipelinePosition > index + 1
          const activePhase = !analysisDone && busy && Math.min(4, Math.max(0, pipelinePosition - 1)) === index
          return <div key={title} className={`${done ? 'done' : ''} ${activePhase ? 'active' : ''}`}><span>{done ? <Check size={14}/> : index + 1}</span><b>{title}</b><small>{activePhase ? humanizePipelineStage(status.stage || hint) : hint}</small></div>
        })}
      </section>
      {!busy && !analysisDone && <section className={`uCard uPreflight ${smartPreflight?.can_start ? 'ready' : smartPreflight ? 'warning' : ''}`}>
        <div className="uCardHead"><div><span>Перед запуском</span><h3>{smartPreflight?.can_start ? 'Проект готов' : smartPreflight ? 'Нужно проверить один или несколько пунктов' : 'Быстрая проверка проекта'}</h3><p>{smartPreflight?.recommendation || 'Проверим видео, свободное место, видеодвижок и локальный AI.'}</p></div><button onClick={runSmartPreflight} disabled={!project}>{smartPreflight ? 'Проверить снова' : 'Проверить'}</button></div>
        <div className="analysisSummary">
          <span><Check size={16}/><b>{project?.name || 'Проект'}</b><small>проект</small></span>
          <span className={sourceReady ? '' : 'warning'}>{sourceReady ? <Check size={16}/> : <Clock3 size={16}/>}<b>{sourceReady ? 'Готов' : 'Не готов'}</b><small>источник</small></span>
          <span><Wand2 size={16}/><b>{settings.task_preset_label || 'Сбалансированный'}</b><small>формат</small></span>
          <span><Clock3 size={16}/><b>{settings.target_minutes || 30} мин</b><small>ориентир</small></span>
        </div>
        <div className="pageActionBar"><div><b>Анализ можно безопасно остановить</b><span>Прогресс и контрольные точки сохраняются в проекте.</span></div><button className="primaryStrong" onClick={runOneClickPipeline} disabled={!project}>Начать анализ</button></div>
      </section>}
      {renderTaskActivity()}
      {analysisDone && <section className={`analysisCompleteCard ${candidates.length ? '' : 'warning'}`}><div className="completeIcon"><Check size={24}/></div><div><span>Анализ завершён</span><h2>{candidates.length ? `Найдено ${candidates.length} моментов` : 'Кандидатов пока 0'}</h2><p>{candidates.length ? `AI уже собрал ${segments.length ? `черновую нарезку из ${segments.length} фрагментов` : 'результат анализа'}. Проверка необязательна: открой результат, когда будешь готов.` : 'Анализ действительно завершён, но текущий результат не содержит кандидатов. Монтаж всё равно можно открыть для проверки состояния или вернуться к диагностике/повторному анализу.'}</p></div><div className="buttonRow wrap"><button onClick={() => refreshAll(project?.id)}>Обновить результаты</button><button className="primaryStrong" onClick={() => setActiveStep('review')}>{segments.length ? 'Посмотреть AI-нарезку' : candidates.length ? 'Посмотреть результаты' : 'Открыть результат'}</button></div></section>}
      {productMode === 'pro' && <section className="uCard advancedSection"><div className="uCardHead"><div><span>Расширенный режим</span><h3>Отдельные этапы и восстановление</h3><p>Используй для диагностики или повторного запуска части конвейера.</p></div><button onClick={resumeProject} disabled={!project || busy || !checkpoints?.can_resume}>Продолжить после сбоя</button></div><div className="buttonRow wrap"><button onClick={() => confirmReanalysis('Повторный AI-анализ') && run('analyze')} disabled={!project || busy}>Только AI-анализ</button><button onClick={() => confirmReanalysis('Повторная проверка изображения и OCR') && run('visual-scan')} disabled={!project || busy}>Изображение и OCR</button><button onClick={() => run('benchmark')} disabled={!project || busy}>Тест производительности</button></div></section>}
    </div>
  }


  function renderUnifiedReviewStep() {
    const active = selectedPreview || filteredCandidates[0] || segments[0]
    const activeAdding = Boolean(active && isCandidateAdding(active))
    const filters = [['all','Все'],['funny','Смешное'],['conflict','Конфликт'],['reaction','Реакция'],['donation','Донат/чат'],['sport','Спорт'],['emotion','Эмоция'],['dialog','Диалог'],['weak','Слабые'],['repeat','Повтор'],['silence','Тишина']]
    const selectedKey = active ? clipKey(active, active.type || (segments.some(s => s === active) ? 'final' : 'candidate')) : ''
    const sourceDuration = Math.max(1, Number(project?.duration_seconds || autoRec?.probe?.duration_seconds || autoRec?.duration_seconds || 0), timelineMax, ...candidates.map(c => Number(c.end) || 0))
    const targetSec = Math.max(1, Number(settings.target_minutes || 0) * 60)
    const montageFill = Math.min(100, (totalFinalDuration / targetSec) * 100)
    const finalClips = segments.map((s, i) => ({ ...s, _idx: i, _type: 'final' }))
    const candidateClips = filteredCandidates.slice(0, reviewVisibleCount).map((c, i) => ({ ...c, _idx: i, _type: 'candidate' }))

    if (analysisCurrent && !busy && candidates.length === 0 && segments.length === 0) {
      return <div className="uPage reviewPage">
        <StepHeader eyebrow="Шаг 4 из 5" title="Результат анализа" description="AI завершил анализ, но готовая нарезка пока пуста. Здесь можно проверить состояние и запустить анализ повторно." />
        <section className="uCard blockedStepCard reviewEmptyState">
          <CircleHelp size={26}/>
          <span>Анализ завершён</span>
          <h2>Кандидаты не найдены или ещё не подгрузились</h2>
          <p>Highlight Studio больше не выталкивает тебя обратно в «Анализ». Сначала обнови сохранённые результаты. Если по-прежнему 0 — открой диагностику или повтори анализ с теми же качественными настройками.</p>
          <div className="buttonRow wrap">
            <button onClick={() => refreshAll(project?.id)}>Обновить результаты</button>
            <button onClick={() => setActiveStep('analysis')}>Вернуться к анализу</button>
            <button className="primaryStrong" onClick={() => setActiveStep('reports')}>Открыть диагностику</button>
          </div>
        </section>
      </div>
    }

    function renderTimelineItems(items, type = 'final') {
      if (!items.length) return <div className="uTimelineEmpty">После AI-анализа здесь появятся микро-нарезки по времени исходного видео.</div>
      return items.map((item, i) => {
        const start = Math.max(0, Number(item.start) || 0)
        const dur = Math.max(1, clipDuration(item))
        const left = Math.max(0, Math.min(99, (start / sourceDuration) * 100))
        const width = Math.max(1.2, Math.min(35, (dur / sourceDuration) * 100))
        const isActive = active && String(active.id ?? active.start) === String(item.id ?? item.start) && Math.abs((Number(active.start)||0) - start) < 0.01
        return <button
          key={`${type}-${item.id ?? i}-${start}`}
          className={`uTimelineClip ${type} ${isActive ? 'active' : ''}`}
          style={{ left: `${left}%`, width: `${width}%` }}
          title={`${secondsToTc(start)} — ${secondsToTc(Number(item.end)||start+dur)} · ${item.title || item.moment_type || type}`}
          aria-label={`${type === 'final' ? 'Фрагмент монтажа' : 'Найденный момент'} ${i + 1}: ${item.title || item.moment_type || 'без названия'}, ${secondsToTc(start)} — ${secondsToTc(Number(item.end) || start + dur)}`}
          onClick={() => preview(item, type)}
        >
          <span>{i + 1}</span>
        </button>
      })
    }

    function renderMomentCard(item, type = 'candidate', index = 0) {
      const key = clipKey(item, type)
      const isExpanded = expandedPreviewKey === key
      const isActive = active && key === selectedKey
      const isAdding = type === 'candidate' && isCandidateAdding(item)
      const explanation = buildLocalMomentExplanation(item, settings)
      return <div key={key} className={`uMomentCard ${isActive ? 'active' : ''}`}>
        <button className="uMomentMain" aria-label={`Открыть ${item.title || item.moment_type || `момент ${index + 1}`} в плеере`} onClick={() => preview(item, type)}>
          <div className="uMomentNumber">{index + 1}</div>
          <div className="uMomentBody">
            <b>{item.title || item.moment_type || (type === 'final' ? 'Финальный фрагмент' : 'AI микро-нарезка')}</b>
            <span>{secondsToTc(item.start)} — {secondsToTc(item.end)} · {formatDuration(clipDuration(item))} · score {item.score ?? '—'}</span>
            <p>{item.what_happens || item.reason || item.text_preview || explanation.what}</p>
            <small>{item.viewer_value || item.why_selected || explanation.value}</small>
          </div>
        </button>
        <div className="uMomentActions">
          <button onClick={() => preview(item, type)}>В плеер</button>
          <button onClick={() => setExpandedPreviewKey(isExpanded ? null : key)}>{isExpanded ? 'Скрыть детали' : 'Подробнее'}</button>
          {type === 'candidate' ? <button className={`addAction ${isAdding ? 'isPending' : ''}`} aria-busy={isAdding ? 'true' : 'false'} disabled={isAdding || isInMontage(item)} onClick={() => addCandidate(item)}>{isAdding ? 'Добавляю…' : isInMontage(item) ? 'В монтаже ✓' : 'В монтаж'}</button> : null}
          {type === 'final' ? <button className="dangerBtn" onClick={() => removeSegment(item._idx ?? index)}>Удалить</button> : null}
        </div>
        {isExpanded ? <div className="uInlinePlayer"><div><b>Микро-нарезка #{index + 1}</b><span>{secondsToTc(item.start)} — {secondsToTc(item.end)}</span><small>Фрагмент открыт в основном стабильном плеере справа.</small></div></div> : null}
      </div>
    }

    return <div className="uPage reviewPage">
      <StepHeader eyebrow="Шаг 4 из 5 · проверка необязательна" title={activeTab === 'final' ? 'AI-нарезка готова' : 'Альтернативные моменты'} description={activeTab === 'final' ? 'AI уже собрал черновой ролик. Проверь только то, что хочешь изменить, или сразу переходи к экспорту.' : 'Здесь дополнительные сильные моменты, которыми можно заменить или дополнить итоговую нарезку.'} />

      {(productMode === 'pro' || (aiQualityAudit && Number(aiQualityAudit.score || 100) < 70)) && <section className="uCard reviewAuditStrip">
        <div className="uCardHead">
          <div><span>AI Quality</span><h3>{aiQualityAudit ? `${aiQualityAudit.score}/100 · ${aiQualityAudit.label}` : 'Качество кандидатов ещё не проверено'}</h3><p>{aiQualityAudit?.recommendations?.[0] || 'Проверь объяснения моментов и добавь недостающие причины выбора перед рендером.'}</p></div>
          <div className="buttonRow wrap"><button onClick={refreshAiQualityAudit} disabled={!project}>Проверить</button><button onClick={backfillExplanations} disabled={!project || (!candidates.length && !segments.length)}>Добавить объяснения</button></div>
        </div>
      </section>}

      <div className="uReviewStudioGrid">
        <section className="uCard uClipsPanel">
          <div className="uToolbar">
            <input placeholder="Поиск по моментам" value={search} onChange={e=>setSearch(e.target.value)} />
            <div className="uFilterLine" aria-label="Фильтр моментов">{filters.map(([id,label]) => <button key={id} aria-pressed={reviewFilter === id} className={reviewFilter === id ? 'active' : ''} onClick={() => setReviewFilter(id)}>{label}</button>)}</div>
            <div className="reviewHistoryActions"><button onClick={undoSegments} disabled={!segmentUndoStack.length}><RotateCcw size={14}/> Отменить</button><button onClick={redoSegments} disabled={!segmentRedoStack.length}><RotateCw size={14}/> Вернуть</button></div><div className="tabSwitch clean" role="tablist" aria-label="Режим Review Studio"><button role="tab" aria-selected={activeTab === 'candidates'} className={activeTab === 'candidates' ? 'active' : ''} onClick={() => { setReviewTabTouched(true); setActiveTab('candidates') }}>Альтернативы ({filteredCandidates.length})</button><button role="tab" aria-selected={activeTab === 'final'} className={activeTab === 'final' ? 'active' : ''} onClick={() => { setReviewTabTouched(true); setActiveTab('final') }}>Итоговая нарезка ({segments.length})</button></div>
          </div>
          <div className="uMomentList upgraded">
            {activeTab === 'final'
              ? (finalClips.length ? finalClips.map((s, i) => renderMomentCard(s, 'final', i)) : <p className="muted">AI-нарезка пока пуста. Открой «Альтернативы» и добавь подходящие моменты.</p>)
              : (candidateClips.length ? candidateClips.map((c, i) => renderMomentCard(c, 'candidate', i)) : <p className="muted">Альтернативные моменты появятся после анализа.</p>)}
          </div>{activeTab === 'candidates' && filteredCandidates.length > candidateClips.length && <div className="loadMoreRow"><span>Показано {candidateClips.length} из {filteredCandidates.length}</span><button onClick={()=>setReviewVisibleCount(v=>v+60)}>Показать ещё</button></div>}
        </section>

        <section className="uCard uPreviewPanel">
          <div className="uCardHead"><div><span>Clip player</span><h3>{active?.title || active?.moment_type || 'Выбери микро-нарезку'}</h3><p>{active ? `${secondsToTc(active.start)} — ${secondsToTc(active.end)} · ${formatDuration(clipDuration(active))}` : 'Плеер будет показывать выбранный фрагмент из исходного видео.'}</p></div></div>
          {active && project ? <div className="clipWorkbench">
            <div className="clipPreviewSurface">
              <div className="clipPreviewViewport">
                {clipPreview.loading ? <div className="playerStatusCard"><div className="playerSpinner"></div><b>Подготавливаю стабильный preview…</b><span>Создаётся короткий H.264-фрагмент вместо повторного поиска по многочасовому VOD.</span></div> : null}
                {clipPreview.error ? <div className="playerStatusCard error"><b>Фрагмент не открылся</b><span>{clipPreview.error}</span><button onClick={() => setClipPreviewReload(v => v + 1)}>Пересобрать preview</button></div> : null}
                {clipPreview.url ? <video key={clipPreview.url} ref={reviewVideoRef} controls preload="metadata" src={clipPreview.url} className="uMainClipPlayer" onError={() => { if (clipPreviewErrorRetries.current < 1) { clipPreviewErrorRetries.current += 1; setClipPreviewReload(v => v + 1) } else { setClipPreview(state => ({ ...state, url: '', error: 'Браузер не смог декодировать preview. Нажми «Пересобрать preview».' })) } }} /> : null}
                {!clipPreview.loading && !clipPreview.error && !clipPreview.url ? <div className="playerEmptyState"><Play size={28}/><b>Preview выбранного момента</b><span>Нажми «Перезагрузить плеер», если фрагмент не появился автоматически.</span></div> : null}
              </div>
              <div className="uClipJumpRow"><button disabled={!clipPreview.url} onClick={() => { const v = reviewVideoRef.current; if (v) { v.currentTime = 0; v.play?.().catch(()=>{}) }}}><Play size={14}/> С начала</button><button disabled={!clipPreview.url} onClick={() => { const v = reviewVideoRef.current; if (v) v.currentTime = Math.max(0, v.currentTime - 5) }}>−5 сек</button><button disabled={!clipPreview.url} onClick={() => { const v = reviewVideoRef.current; if (v) v.currentTime = Math.max(0, v.duration - 0.1) }}>К концу</button><button onClick={() => setClipPreviewReload(v => v + 1)}><RefreshCw size={13}/> Обновить</button></div>
            </div>
            <aside className="clipInspectorPanel">
              <div className="inspectorHeading"><span>Инспектор момента</span><b>AI-объяснение и границы</b></div>
              <div className="uExplain improved"><b>Что происходит</b><p>{active.what_happens || active.text_preview || buildLocalMomentExplanation(active, settings).what}</p><b>Почему выбрано</b><p>{active.why_selected || active.reason || active.ai_explanation || buildLocalMomentExplanation(active, settings).why}</p><b>Ценность для зрителя</b><p>{active.viewer_value || buildLocalMomentExplanation(active, settings).value}</p><b>Риск</b><p>{active.risk || buildLocalMomentExplanation(active, settings).risk}</p></div>
              <div className="trimEditor"><label><span>Начало</span><input type="number" step="0.1" min="0" value={trimDraft.start} onChange={e=>setTrimDraft({...trimDraft,start:e.target.value})}/></label><label><span>Конец</span><input type="number" step="0.1" min="0.5" value={trimDraft.end} onChange={e=>setTrimDraft({...trimDraft,end:e.target.value})}/></label><button onClick={applyTrimDraft}>Сохранить</button></div>
              <div className="inspectorActions"><button className={`primary ${activeAdding ? 'isPending' : ''}`} aria-busy={activeAdding ? 'true' : 'false'} disabled={activeAdding || isInMontage(active)} onClick={() => addCandidate(active)}>{activeAdding ? 'Добавляю и сохраняю…' : isInMontage(active)?'Уже в итоговой нарезке ✓':'Добавить в итоговую нарезку'}</button><button onClick={() => upsertAdjustedClip(active,{start:Math.max(0,(Number(active.start)||0)-5)})}>Расширить начало</button><button onClick={() => upsertAdjustedClip(active,{end:(Number(active.end)||0)+5})}>Расширить конец</button><button onClick={() => sendFeedback(active,'good','best')}>Отметить сильным</button><button className="dangerBtn" onClick={() => rejectClip(active)}>Отклонить</button></div>
            </aside>
          </div> : <p className="muted">Сначала выбери проект и микро-нарезку.</p>}
        </section>
      </div>
      <section className="uCard uTimelinePanel editorTimelinePanel">
        <div className="uCardHead">
          <div><span>Timeline</span><h3>Итоговая нарезка относительно исходного видео</h3><p>Верхняя дорожка — AI-черновик с твоими правками, нижняя — альтернативные моменты.</p></div>
          <div className="uTimelineSummary"><b>{formatDuration(totalFinalDuration)}</b><small>из ориентира {settings.target_minutes || 0} мин</small></div>
        </div>
        <div className="uDurationMeter"><div style={{ width: `${montageFill}%` }}></div></div>
        <div className="uTimelineScale"><span>00:00</span><span>{secondsToTc(sourceDuration / 2)}</span><span>{secondsToTc(sourceDuration)}</span></div>
        <div className="uTimelineTrack final"><label>Итоговая нарезка</label><div>{renderTimelineItems(finalClips, 'final')}</div></div>
        <div className="uTimelineTrack candidates"><label>Альтернативы</label><div>{renderTimelineItems(candidateClips, 'candidate')}</div></div>
      </section>
      {segments.length > 0 && <div className={`reviewSelectionBar ${pendingCandidateKeys.length ? 'saving' : ''}`} aria-live="polite"><div><b>{segments.length} фрагментов · {formatDuration(totalFinalDuration)}</b><span>{pendingCandidateKeys.length ? 'Сохраняю выбранный фрагмент…' : 'Изменения сохранены в проекте'}</span></div><button className="primaryStrong" disabled={pendingCandidateKeys.length > 0} onClick={() => setActiveStep('export')}>{pendingCandidateKeys.length ? 'Подождите сохранения…' : 'Продолжить к экспорту'}</button></div>}
    </div>
  }

  function renderStableProfileCard() {
    const diffs = qualityLock?.critical_diffs || []
    const savedAt = qualityLock?.saved_at ? new Date(qualityLock.saved_at * 1000).toLocaleString() : '—'
    return <section className={`uCard stableProfileCard ${qualityLock?.active ? 'locked' : qualityLock?.exists ? 'warn' : ''}`}>
      <div className="uCardHead"><div><span>Рабочий профиль</span><h3>{qualityLock?.exists ? 'Мой рабочий пресет' : 'Зафиксируй лучшие настройки'}</h3><p>{qualityLock?.message || 'Сохрани текущие настройки как рабочий профиль, чтобы случайно не сбить качество AI-нарезки.'}</p></div><div className={qualityLock?.active ? 'safeBadge ok' : qualityLock?.exists ? 'safeBadge warn' : 'safeBadge'}>{qualityLock?.active ? 'ACTIVE' : qualityLock?.exists ? `${diffs.length} изменений` : 'NEW'}</div></div>
      <div className="uProjectStats"><div><b>{stableCandidate?.name ? 'v1.0' : '—'}</b><span>версия профиля</span></div><div><b>{savedAt}</b><span>профиль сохранён</span></div><div><b>{qualityLock?.best_fingerprint || '—'}</b><span>сохранённая конфигурация</span></div><div><b>{qualityLock?.fingerprint || '—'}</b><span>текущая конфигурация</span></div></div>
      {diffs.length ? <ul className="reportList compact">{diffs.slice(0,6).map(x => <li key={x.key}><b>{x.key}</b>: сейчас <code>{String(x.current)}</code>, в рабочем <code>{String(x.best)}</code></li>)}</ul> : <p className="muted">Когда профиль активен, AI-настройки совпадают с проверенной конфигурацией. Это защищает качество роликов.</p>}
      <div className="buttonRow wrap"><button className="primary" onClick={saveMyBestSettings}>Сохранить как “Мой рабочий пресет”</button><button onClick={applyMyBestSettings} disabled={!(qualityLock?.exists || bestProfile)}>Применить рабочий пресет</button><button onClick={() => setActiveStep('settings')}>Настройки</button></div>
    </section>
  }

  async function copyText(value, label) {
    if (!value) return setError(`${label}: пока нечего копировать.`)
    try { await navigator.clipboard.writeText(String(value)); setError(`${label} скопировано.`) }
    catch (_) { setError(`Не удалось скопировать ${label.toLowerCase()}.`) }
  }

  function renderFinalSuccessCard() {
    if (!finalSuccess?.final_ready && !finalReady) return null
    const pack=creatorPack||finalSuccess?.creator_pack||{}
    return <section className="uCard finalSuccessCard simplifiedResult"><div className="resultCheckmark"><Check size={24}/></div><div><span className="uBadge">ГОТОВО</span><h2>{finalSuccess?.title || 'Видео готово'}</h2><p>{finalSuccess?.message || 'Итоговый ролик собран. Теперь его можно открыть, создать Shorts или опубликовать.'}</p><div className="resultMainActions"><button className="primaryStrong heroAction" onClick={openFinalVideo}>Открыть видео</button><button onClick={showProjectFolder}>Открыть папку</button><button onClick={() => setExportView('shorts')} disabled={!project || busy || !segments.length}>Создать Shorts</button><button onClick={() => {setExportView('youtube'); if (!creatorPack) recomputeCreatorPack()}}>Опубликовать</button></div>{pack?.titles?.length ? <div className="publishQuickActions"><button onClick={() => copyText(pack.titles[0], 'Название')}><Copy size={14}/> Название</button><button onClick={() => copyText(pack.description, 'Описание')}><Copy size={14}/> Описание</button><button onClick={() => copyText((pack.tags || []).join(', '), 'Теги')}><Copy size={14}/> Теги</button></div> : null}</div></section>
  }


  function renderSuccessHistoryCard() {
    const items = successHistory?.length ? successHistory : projectHistory?.filter?.(x => x.final_file) || []
    if (!items.length) return <section className="uCard successHistoryCard"><div className="uCardHead"><div><span>История удачных проектов</span><h3>Пока нет готовых роликов</h3><p>Когда финальный render появится в outputs, проект попадёт в историю успешных работ.</p></div></div></section>
    return <section className="uCard successHistoryCard"><div className="uCardHead"><div><span>История удачных проектов</span><h3>{items.length} готовых роликов</h3><p>Смотри, какие настройки дали хороший результат, и быстро возвращайся к успешным проектам.</p></div></div><div className="uHistory success">{items.slice(0,8).map(h => <button key={h.id} onClick={() => loadProjectById(h.id)}><b>{h.name}</b><span>{h.preset} · {h.duration} · {h.candidates} кандидатов · {h.segments} в финале</span><small>{h.final_file || h.project_folder}</small></button>)}</div></section>
  }

  function renderTaskActivity() {
    if (!busy && status.state !== 'error' && status.state !== 'cancelled') return null
    return <div className="taskActivityLink"><span>{busy ? 'Задача выполняется в фоне. Можно переходить между разделами.' : status.state === 'error' ? 'Задача завершилась с ошибкой. Открой подробности.' : 'Задача остановлена.'}</span><button type="button" onClick={() => setTaskCenterOpen(true)}>Открыть задачи</button></div>
  }

  function renderTaskDetails() {
    const recent = (statusHistory || []).slice(-8).reverse()
    const logLines = String(logs || '').split('\n').filter(Boolean).slice(-18)
    return <div className="taskDetailsLog">{status.message && <p>{status.message}</p>}{recent.length > 0 && <details><summary>Последние этапы</summary>{recent.map((row,index) => <p key={index}>{row.message || row.stage}</p>)}</details>}{productMode === 'pro' && logLines.length > 0 && <details><summary>Журнал задачи</summary><pre className="logs">{logLines.join('\n')}</pre></details>}</div>
  }

  function renderYoutubePublishCard() {
    const connected = Boolean(youtubeStatus?.connected)
    const selectedOutput = youtubeVideoOutputs.find(item => item.path === youtubeForm.file_path)
    const uploaded = youtubeReport?.uploaded || []
    const failed = youtubeReport?.failed || []
    const privacyLabel = youtubeForm.privacy_status === 'public' ? 'публично' : youtubeForm.privacy_status === 'unlisted' ? 'по ссылке' : 'приватно'
    const youtubeTaskActive = youtubeBusy || (busy && String(status.stage || status.message || '').toLowerCase().includes('youtube'))
    return <section className="youtubePublishSurface">
      <div className="youtubeChannelBar">
        <span className="youtubeBrandMark" aria-hidden="true">▶</span>
        <div><span>Публикация на YouTube</span><h3>{connected ? youtubeStatus.channel_title || 'YouTube-канал подключён' : 'Подключи канал один раз'}</h3><p>Авторизация проходит через официальный OAuth. Пароль Google не передаётся приложению.</p></div>
        <span className={`connectionBadge ${connected ? 'connected' : ''}`}>{connected ? 'Подключён' : 'Не подключён'}</span>
      </div>
      {!youtubeVideoOutputs.length && <div className="publishPrerequisite"><Film size={20}/><div><b>Сначала создай готовое видео или Shorts</b><span>После рендера файлы автоматически появятся здесь.</span></div><button onClick={() => setExportView('video')}>К экспорту</button></div>}
      {!youtubeStatus?.dependencies_available && <div className="inlineIssue error"><b>Компоненты YouTube не установлены</b><span>Запусти `INSTALL_YOUTUBE_PUBLISH.bat`, затем повтори проверку.</span></div>}
      {!youtubeStatus?.credentials_configured && youtubeStatus?.dependencies_available && <div className="youtubeConnectPanel"><input ref={youtubeCredentialsInputRef} type="file" accept="application/json,.json" hidden onChange={uploadYoutubeCredentials}/><div><b>Добавь OAuth-файл Desktop app</b><span>Файл хранится локально и нужен только для подключения канала.</span></div><button className="primaryStrong" onClick={() => youtubeCredentialsInputRef.current?.click()} disabled={youtubeBusy}>Выбрать OAuth JSON</button></div>}
      {youtubeStatus?.credentials_configured && !connected && <div className="youtubeConnectPanel"><div><b>OAuth-файл готов</b><span>Откроется безопасное окно входа Google.</span></div><button className="primaryStrong" onClick={connectYoutube} disabled={youtubeBusy}>{youtubeBusy ? 'Подключаем…' : 'Подключить канал'}</button><button onClick={() => refreshYoutubeStatus(true)} disabled={youtubeBusy}>Проверить статус</button></div>}
      {connected && youtubeVideoOutputs.length > 0 && <>
        <div className="youtubePublishGrid">
          <label><span>Готовый файл</span><select value={youtubeForm.file_path} onChange={event => setYoutubeForm(previous => ({ ...previous, file_path: event.target.value }))}><option value="">Выбери видео</option>{youtubeVideoOutputs.map(item => <option key={item.path} value={item.path}>{item.kind === 'short' ? 'Shorts' : 'Видео'} · {item.name} · {item.size_mb} MB</option>)}</select></label>
          <label><span>Доступ после загрузки</span><select value={youtubeForm.privacy_status} onChange={event => setYoutubeForm(previous => ({ ...previous, privacy_status: event.target.value }))}><option value="private">Приватное — рекомендуется</option><option value="unlisted">По ссылке</option><option value="public">Публичное</option></select></label>
          <label className="wide"><span>Название</span><input maxLength={100} value={youtubeForm.title} onChange={event => setYoutubeForm(previous => ({ ...previous, title: event.target.value }))} placeholder="Название ролика"/></label>
          <label className="wide"><span>Описание</span><textarea rows={5} maxLength={5000} value={youtubeForm.description} onChange={event => setYoutubeForm(previous => ({ ...previous, description: event.target.value }))} placeholder="Описание видео"/></label>
          <label className="wide"><span>Теги через запятую</span><input value={youtubeForm.tags} onChange={event => setYoutubeForm(previous => ({ ...previous, tags: event.target.value }))} placeholder="стрим, нарезка, реакции"/></label>
        </div>
        <div className="youtubeOptions"><label><input type="checkbox" checked={youtubeForm.notify_subscribers} onChange={event => setYoutubeForm(previous => ({ ...previous, notify_subscribers: event.target.checked }))}/> Уведомить подписчиков</label><label><input type="checkbox" checked={youtubeForm.made_for_kids} onChange={event => setYoutubeForm(previous => ({ ...previous, made_for_kids: event.target.checked }))}/> Контент для детей</label><label><input type="checkbox" checked={youtubeForm.contains_synthetic_media} onChange={event => setYoutubeForm(previous => ({ ...previous, contains_synthetic_media: event.target.checked }))}/> Есть реалистичный изменённый или AI-контент</label></div>
        <div className="publishSafetySummary"><ShieldCheck size={18}/><div><b>Перед отправкой</b><span>{selectedOutput ? selectedOutput.name : 'Файл не выбран'} · загрузка {privacyLabel}{youtubeForm.notify_subscribers ? ' · с уведомлением подписчиков' : ' · без уведомления подписчиков'}</span></div></div>
        {youtubeTaskActive && renderTaskActivity()}
        <div className="pageActionBar publishActions"><div><button onClick={() => fillYoutubeMetadata(youtubeForm.file_path)} disabled={!selectedOutput}>Заполнить из Creator Pack</button><button onClick={uploadAllShortsYoutube} disabled={youtubeBusy || busy || !youtubeShortOutputs.length}>Загрузить все Shorts ({youtubeShortOutputs.length})</button></div><button className="primaryStrong" onClick={uploadSelectedYoutube} disabled={youtubeBusy || busy || !youtubeForm.file_path || !youtubeForm.title}>{youtubeBusy ? 'Загружаем…' : `Загрузить ${privacyLabel}`}</button></div>
        <div className="youtubeAccountActions"><button className="textButton" onClick={() => refreshYoutubeStatus(true)} disabled={youtubeBusy}>Обновить канал</button><button className="textButton dangerText" onClick={disconnectYoutube} disabled={youtubeBusy}>Отключить канал</button></div>
      </>}
      {(uploaded.length > 0 || failed.length > 0) && <div className="youtubeResults"><div className="sectionHeading"><div><span className="sectionKicker">Последняя загрузка</span><h3>Результаты</h3></div></div>{uploaded.map(item => <p className="uploadResult successResult" key={item.video_id}><Check size={16}/><span><b>{item.title}</b><a href={item.youtube_url} target="_blank" rel="noreferrer">Открыть на YouTube</a></span></p>)}{failed.map((item, index) => <div className="uploadResult failedResult" key={`${item.file_path}-${index}`}><X size={16}/><span><b>{item.file_path}</b><small>{item.error}</small></span><button onClick={uploadSelectedYoutube}>Повторить</button></div>)}</div>}
    </section>
  }


  function renderUnifiedExportStep() {
    const duration = durationControl || {}
    const renderReadiness = renderQuality || preRenderReport || {}
    const shortsOutputs = youtubeShortOutputs
    const shortCandidates = Array.isArray(factory?.shorts) ? factory.shorts : []
    return <div className="uPage exportPage"><StepHeader eyebrow="Шаг 5 из 5" title={finalReady ? 'Результат и публикация' : 'Экспортировать видео'} description={finalReady ? 'Открой готовый файл, создай Shorts или опубликуй результат.' : 'Проверь итоговую длительность и запусти рендер.'}/>
      <div className="exportTabs" role="tablist" aria-label="Результаты и публикация">
        <button role="tab" aria-selected={exportView === 'video'} className={exportView === 'video' ? 'active' : ''} onClick={() => setExportView('video')}><Film size={17}/> Видео</button>
        <button role="tab" aria-selected={exportView === 'shorts'} className={exportView === 'shorts' ? 'active' : ''} onClick={() => setExportView('shorts')}><Scissors size={17}/> Shorts {shortsOutputs.length ? <span>{shortsOutputs.length}</span> : null}</button>
        <button role="tab" aria-selected={exportView === 'youtube'} className={exportView === 'youtube' ? 'active' : ''} onClick={() => setExportView('youtube')}><span className="miniYoutubeMark">▶</span> YouTube</button>
      </div>

      {exportView === 'video' && <>
        {renderFinalSuccessCard()}
        {!finalReady && <section className="uCard renderReadinessCard">
          <div className="uCardHead"><div><span>Готовность монтажа</span><h3>{renderReadiness.ready === false ? 'Нужно исправить проблемы' : 'Можно собирать видео'}</h3><p>{renderReadiness.summary || `В монтаже ${segments.length} фрагментов общей длительностью ${formatDuration(totalFinalDuration)}.`}</p></div><button onClick={() => recomputeReport('pre-render-check')} disabled={!project}>Проверить</button></div>
          <div className="exportSummary">
            <span><b>{segments.length}</b><small>фрагментов</small></span>
            <span><b>{formatDuration(totalFinalDuration)}</b><small>итоговая длительность</small></span>
            <span className={Number(renderReadiness.duplicates || 0) ? 'warning' : ''}><b>{renderReadiness.duplicates || 0}</b><small>повторов</small></span>
            <span className={Number(renderReadiness.missing_files || 0) ? 'warning' : ''}><b>{renderReadiness.missing_files || 0}</b><small>проблем с файлами</small></span>
          </div>
          <div className="pageActionBar"><div><b>{formatDuration(totalFinalDuration)} из цели {settings.target_minutes || 30} мин</b><span>{duration.message || 'Лучше короче и сильнее, чем точно попадать в заданную длительность.'}</span></div><button className="primaryStrong" onClick={() => run('render')} disabled={!project || busy || !sourceReady || !segments.length}>{busy ? 'Видео собирается…' : 'Собрать итоговое видео'}</button></div>
          {productMode === 'pro' && <div className="uThreeActions"><button onClick={() => applyDurationAction('refill_similar')}>Добрать похожие</button><button onClick={() => applyDurationAction('lower_score')}>Снизить порог</button><button onClick={() => applyDurationAction('add_context')}>Добавить контекст</button></div>}
        </section>}
        {renderTaskActivity()}
        <section className="uCard creatorPackCard"><div className="uCardHead"><div><span>Creator Pack</span><h3>Название, описание и теги</h3><p>Подготовь метаданные до перехода к публикации.</p></div><button onClick={recomputeCreatorPack} disabled={!project}>{creatorPack ? 'Обновить' : 'Подготовить'}</button></div>{creatorPack ? <div className="uCreatorGrid"><div><b>Названия</b><ol>{(creatorPack.titles || []).slice(0, 5).map((title, index) => <li key={index}>{title} <button className="copyInline" aria-label={`Скопировать название ${index + 1}`} onClick={() => copyText(title, 'Название')}><Copy size={12}/></button></li>)}</ol></div><div><b>Описание</b><p>{creatorPack.description}</p><button onClick={() => copyText(creatorPack.description, 'Описание')}>Скопировать описание</button></div><div><b>Теги</b><p>{(creatorPack.tags || []).join(', ')}</p><button onClick={() => copyText((creatorPack.tags || []).join(', '), 'Теги')}>Скопировать теги</button></div></div> : <div className="compactEmptyState">Метаданные появятся после подготовки.</div>}</section>
      </>}

      {exportView === 'shorts' && <ShortsStudio key={project?.id || 'empty'} projectId={project?.id || ''}
        candidates={shortCandidates} outputs={shortsOutputs} settings={settings} status={status} busy={busy}
        sourceDuration={Number(project?.duration_seconds || autoRec?.probe?.duration_seconds || autoRec?.duration_seconds || 0)}
        canGenerate={Boolean(project && segments.length)} authQuery={authQuery}
        onSave={(index,payload)=>submitShort(index,payload,false)} onRender={(index,payload)=>submitShort(index,payload,true)}
        onGenerate={generateShorts} onRefresh={()=>refreshAll()} onOpenTasks={()=>setTaskCenterOpen(true)}
        settingsPanel={<>
        <div className="shortsSetupGrid">
          <label><span>Количество Shorts</span><input type="number" min="1" max="50" value={settings.shorts_count || 5} onChange={event => setSettings(previous => ({...previous, shorts_count: Number(event.target.value)}))}/></label>
          <label><span>Вертикальная компоновка</span><select value={settings.shorts_reframe_mode || 'auto'} onChange={event => setSettings(previous => ({...previous, shorts_reframe_mode: event.target.value}))}><option value="auto">Автоматически</option><option value="smart_face">Следить за лицом</option><option value="gameplay_facecam">Игра + веб-камера</option><option value="blur_background">Полный кадр + размытый фон</option><option value="smart_zoom">Увеличенный центр + размытый фон (без слежения)</option><option value="center_crop">Обрезать по центру</option><option value="fit">Полный кадр с полями</option></select></label>
          <label><span>Качество субтитров</span><select value={settings.shorts_caption_quality || 'high'} onChange={event => setSettings(previous => ({...previous, shorts_caption_quality: event.target.value}))}><option value="fast">Быстро — основная расшифровка</option><option value="high">Уточнить речь — дополнительное распознавание</option><option value="max">Точное выравнивание — если компонент установлен</option></select></label>
          <label><span>Размер субтитров</span><input type="number" min="48" max="96" value={settings.shorts_caption_font_size || 72} onChange={event => setSettings(previous => ({...previous, shorts_caption_font_size: Number(event.target.value)}))}/></label>
          <label><span>Слов одновременно</span><input type="number" min="3" max="8" value={settings.shorts_caption_max_words || 4} onChange={event => setSettings(previous => ({...previous, shorts_caption_max_words: Number(event.target.value)}))}/></label>
          <label className="wide"><span>Словарь распознавания</span><input value={settings.shorts_recognition_dictionary || ''} onChange={event => setSettings(previous => ({...previous, shorts_recognition_dictionary: event.target.value}))} placeholder="ники, имена, игры, сленг"/></label>
        </div>
        <div className="shortsOptions">
          <label><input type="checkbox" checked={Boolean(settings.shorts_funny_search_enabled)} onChange={event => setSettings(previous => ({...previous, shorts_funny_search_enabled: event.target.checked}))}/> Искать смешные моменты</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_emotion_events_enabled)} onChange={event => setSettings(previous => ({...previous, shorts_emotion_events_enabled: event.target.checked}))}/> Учитывать смех и эмоции</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_precise_alignment)} onChange={event => setSettings(previous => ({...previous, shorts_precise_alignment: event.target.checked}))}/> Уточнять время каждого слова</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_burn_subtitles)} onChange={event => setSettings(previous => ({...previous, shorts_burn_subtitles: event.target.checked}))}/> Показывать субтитры</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_dynamic_captions)} onChange={event => setSettings(previous => ({...previous, shorts_dynamic_captions: event.target.checked}))}/> Динамические субтитры</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_normalize_audio)} onChange={event => setSettings(previous => ({...previous, shorts_normalize_audio: event.target.checked}))}/> Выравнивать громкость</label>
          <label><input type="checkbox" checked={Boolean(settings.shorts_hook_title_enabled)} onChange={event => setSettings(previous => ({...previous, shorts_hook_title_enabled:event.target.checked}))}/> Заголовок в начале ролика</label>
        </div>
        </>}
      />}

      {exportView === 'youtube' && renderYoutubePublishCard()}
      {productMode === 'pro' && exportView === 'video' && renderSuccessHistoryCard()}
    </div>
  }


  function renderWorkflowGuardCard() {
    const next = workflowGuard?.next_action || {}
    const steps = workflowGuard?.steps || []
    return <section className="uCard workflowGuardCard">
      <div className="uCardHead"><div><span>Workflow Guard</span><h3>{next.label || 'Следующее действие'}</h3><p>{next.action || 'Показывает, что сейчас делать дальше и не пропущен ли важный шаг.'}</p></div><button className="primary" onClick={() => next.step ? setActiveStep(next.step) : refreshAll(project?.id)} disabled={!project}>{next.step ? 'Перейти' : 'Обновить'}</button></div>
      <div className="readinessChecklist compact">{steps.length ? steps.map(x => <div key={x.id} className={x.ok ? 'readyItem ok' : 'readyItem warning'}><b>{x.ok ? '✓' : '!'}</b><span>{x.label}</span><small>{x.details}</small></div>) : <p className="muted wide">Открой проект, чтобы увидеть workflow-проверку.</p>}</div>
    </section>
  }

  function renderPackagingAuditCard() {
    const checks = appAudit?.checks || []
    const bad = checks.filter(x => !x.ok)
    return <section className="uCard releaseAuditCard">
      <div className="uCardHead"><div><span>Packaging Audit</span><h3>{appAudit?.title || 'Проверка сборки приложения'}</h3><p>{appAudit?.summary || 'Проверяет комплектность ZIP, но не заменяет тесты Twitch, AI, GPU и длинных видео.'}</p></div><div className={appAudit?.ok ? 'safeBadge ok' : 'safeBadge warn'}>{appAudit?.score ?? '—'}/100</div></div>
      <div className="readinessMeter"><i style={{width:`${Math.max(0, Math.min(100, Number(appAudit?.score || 0)))}%`}} /></div>
      {bad.length ? <ul className="reportList compact">{bad.slice(0,8).map(x => <li key={x.id}><b>{x.label}</b>: {x.details}</li>)}</ul> : <p className="muted">Критичных проблем упаковки не найдено.</p>}
      {appAudit?.next_actions?.length ? <p className="muted">{appAudit.next_actions[0]}</p> : null}
    </section>
  }

  function renderArtifactCard() {
    const checks = renderArtifactCheck?.checks || []
    return <section className="uCard artifactCheckCard">
      <div className="uCardHead"><div><span>Render Artifact Check</span><h3>{renderArtifactCheck?.final_ready ? 'Итоговый файл найден' : 'Итоговый файл ещё не готов'}</h3><p>Проверяет наличие highlight_final.mp4, Shorts и готовых output-файлов после рендера.</p></div><div className={renderArtifactCheck?.final_ready ? 'safeBadge ok' : 'safeBadge warn'}>{renderArtifactCheck?.outputs_count ?? '—'} файлов</div></div>
      <div className="readinessChecklist compact">{checks.length ? checks.map(x => <div key={x.id} className={x.ok ? 'readyItem ok' : 'readyItem warning'}><b>{x.ok ? '✓' : '!'}</b><span>{x.label}</span><small>{x.details}</small></div>) : <p className="muted wide">После рендера здесь появится проверка output-файлов.</p>}</div>
    </section>
  }

  function renderUnifiedReportsStep() {
    const integrityIssues=integrity?.issues||[], corrupted=(integrity?.files||[]).filter(x=>x.exists&&!x.valid), recoverable=corrupted.filter(x=>x.recoverable).length
    return <div className="uPage"><StepHeader eyebrow="Диагностика" title="Состояние проекта" description="Проверь исходник, сохранённые данные и качество готового монтажа. Технические инструменты доступны только в режиме «Профи»." />
      <div className="uReportGrid userReports"><section className="uCard"><div className="uCardHead"><div><span>Проект</span><h3>{integrity?.ok?'Файлы проекта в порядке':'Нужна проверка'}</h3><p>{integrity?.message||'Проверим исходное видео и сохранённые данные проекта.'}</p></div><div className={integrity?.ok?'safeBadge ok':'safeBadge warn'}>{integrity?.ok?'OK':`${integrityIssues.length} проблем`}</div></div><div className="buttonRow"><button onClick={()=>refreshAll(project?.id)} disabled={!project}>Проверить</button><button className="primaryStrong" onClick={repairJsonBackups} disabled={!project||!recoverable}>Восстановить данные</button></div></section>
      <section className="uCard"><div className="uCardHead"><div><span>Качество моментов</span><h3>{aiQualityAudit?`${aiQualityAudit.score}/100 · ${aiQualityAudit.label}`:'Проверка ещё не выполнена'}</h3><p>{aiQualityAudit?.recommendations?.[0]||'Покажет слабые фрагменты, повторы и риск обрезанного контекста.'}</p></div></div><button onClick={refreshAiQualityAudit} disabled={!project}>Проверить качество</button></section>
      <section className="uCard"><div className="uCardHead"><div><span>Автовосстановление</span><h3>Исправить безопасные проблемы</h3><p>Восстанавливает служебные файлы и нормализует монтаж, не изменяя исходное видео.</p></div></div><button className="primary" onClick={runProjectDoctor} disabled={!project}>Проверить и исправить</button></section></div>
      {productMode==='pro'&&<><button className="textButton diagnosticsToggle" onClick={()=>setDiagnosticsExpanded(v=>!v)}>{diagnosticsExpanded?'Скрыть техническую диагностику':'Открыть техническую диагностику'}</button>{diagnosticsExpanded&&<div className="uReportGrid technicalReports">{renderWorkflowGuardCard()}{renderArtifactCard()}{renderPackagingAuditCard()}<section className="uCard"><h3>Кэш анализа</h3><p>Проверяет совместимость сохранённых данных с видео и настройками.</p><div className="buttonRow"><button onClick={saveCacheFingerprint} disabled={!project}>Сохранить состояние</button><button onClick={clearIncompatibleCache} disabled={!project}>Очистить несовместимое</button></div></section><section className="uCard"><h3>Отладочный пакет</h3><p>Создаёт архив журналов без исходного видео.</p><button onClick={exportDebugBundle} disabled={!project}>Создать пакет</button></section></div>}</>}
      {productMode==='pro'&&logs&&<pre className="logs">{logs}</pre>}
    </div>
  }


  function renderUnifiedSettingsStep() {
    const updateSetting = (key, value) => setSettings(st => ({ ...st, [key]: value }))
    const readNumber = (key, fallback = 0) => Number(settings[key] ?? fallback)
    const boolValue = (key, fallback = false) => String(settings[key] ?? fallback)
    const field = (key, label, type = 'text', extra = {}) => {
      if (settingsQuery.trim()) {
        const hay = `${key} ${label}`.toLowerCase()
        if (!hay.includes(settingsQuery.trim().toLowerCase())) return null
      }
      const value = settings[key]
      const common = { 'data-setting-key': key }
      if (type === 'select') {
        return <label key={key} className={extra.wide ? 'wide' : ''}><span>{label}</span><select {...common} value={value ?? extra.fallback ?? ''} onChange={e => updateSetting(key, extra.parse ? extra.parse(e.target.value) : e.target.value)}>{(extra.options || []).map(opt => Array.isArray(opt) ? <option key={opt[0]} value={opt[0]}>{opt[1]}</option> : <option key={opt} value={opt}>{opt}</option>)}</select>{extra.hint && <small>{extra.hint}</small>}</label>
      }
      if (type === 'bool') {
        return <label key={key} className={extra.wide ? 'wide' : ''}><span>{label}</span><select {...common} value={boolValue(key, extra.fallback)} onChange={e => updateSetting(key, e.target.value === 'true')}><option value="true">Да</option><option value="false">Нет</option></select>{extra.hint && <small>{extra.hint}</small>}</label>
      }
      if (type === 'number') {
        return <label key={key} className={extra.wide ? 'wide' : ''}><span>{label}</span><input {...common} type="number" min={extra.min} max={extra.max} step={extra.step || 1} value={readNumber(key, extra.fallback)} onChange={e => updateSetting(key, Number(e.target.value))}/>{extra.hint && <small>{extra.hint}</small>}</label>
      }
      if (type === 'textarea') {
        return <label key={key} className="wide"><span>{label}</span><textarea {...common} className="promptArea" value={value ?? ''} onChange={e => updateSetting(key, e.target.value)}/>{extra.hint && <small>{extra.hint}</small>}</label>
      }
      return <label key={key} className={extra.wide ? 'wide' : ''}><span>{label}</span><input {...common} value={value ?? ''} onChange={e => updateSetting(key, e.target.value)}/>{extra.hint && <small>{extra.hint}</small>}</label>
    }

    const settingsGroups = {
      main: {
        title: 'Главные настройки',
        description: 'То, что реально нужно менять чаще всего: задача, длительность, режим и профиль ПК.',
        fields: [
          field('content_type', 'Тип контента', 'select', { options: ['Auto','IRL стрим','IRL прогулка/город','Спорт','Подкаст','Shorts'] }),
          field('edit_mode', 'Режим монтажа', 'select', { options: ['Сбалансированный','IRL смешное','IRL конфликт / хаос','Спорт / теннис','Подкаст / разговор','Только Shorts'] }),
          field('target_minutes', 'Цель, минут', 'number', { min: 1, max: 240 }),
          field('auto_target_duration', 'Авто-длительность', 'bool'),
          field('hardware_profile', 'Профиль производительности', 'select', { options: [['Auto','Автоматически по этому ПК'],['LowVRAM','Экономный режим'],['CPU_Stable','Только процессор']] }),
          field('hardware_auto_optimize', 'Автооптимизация железа', 'bool', { hint: 'При включении приложение проверяет реальный CUDA/NVENC runtime и использует безопасный профиль.' }),
          field('cpu_worker_limit', 'Лимит CPU workers', 'number', { min: 0, max: 64, hint: '0 = выбрать автоматически.' }),
          field('gpu_job_limit', 'GPU jobs одновременно', 'number', { min: 1, max: 4 }),
          field('gpu_vram_reserve_mb', 'Резерв VRAM, MB', 'number', { min: 0, max: 8192 }),
          field('analysis_profile', 'Профиль анализа', 'select', { options: ['fast','balanced','quality'] }),
          field('task_preset_label', 'Task-пресет', 'text', { hint: 'Меняется через карточки пресетов на экране “Стиль”.' }),
          field('strict_preflight', 'Строгий preflight', 'bool'),
        ]
      },
      ai: {
        title: 'Ollama / AI',
        description: 'Локальный AI без отправки видео в облако. Эти параметры нужны только опытным пользователям.',
        fields: [
          field('ai_engine', 'AI Engine', 'select', { options: ['ollama'], hint: 'Cloud/OpenAI поля являются legacy и не используются в этой сборке.' }),
          field('ollama_url', 'Ollama URL'),
          field('text_model', 'Text model'),
          field('vision_model', 'Vision model'),
          field('ollama_timeout', 'Ollama timeout', 'number', { min: 60, max: 7200 }),
          field('ollama_keep_alive', 'Keep alive'),
          field('ollama_num_ctx', 'Context size', 'number', { min: 1024, max: 32768 }),
          field('ai_batch_size', 'AI batch', 'number', { min: 1, max: 16 }),
          field('micro_batch_size', 'Micro batch', 'number', { min: 1, max: 32 }),
          field('ai_retry_count', 'AI retries', 'number', { min: 0, max: 20 }),
          field('ai_strict_mode', 'AI strict', 'bool'),
          field('full_ai_coverage', 'Full AI coverage', 'bool'),
          field('temporal_fairness_enabled', 'Равный шанс всему VOD', 'bool', { hint: 'Гарантирует поздним сильным блокам второй AI-проход, но не заставляет брать одинаково из каждой части.' }),
          field('temporal_fairness_bucket_seconds', 'Temporal bucket, sec', 'number', { min: 300, max: 3600 }),
          field('temporal_fairness_blocks_per_bucket', 'Blocks per bucket', 'number', { min: 1, max: 10 }),
          field('micro_global_score_floor', 'Global micro score floor', 'number', { min: 0, max: 10, step: 0.1 }),
          field('hybrid_disable_fallback', 'Disable fallback', 'bool'),
        ]
      },
      video: {
        title: 'Видео / Whisper / Micro-cut',
        description: 'Настройки транскрибации и разбиения длинного стрима на управляемые блоки.',
        fields: [
          field('whisper_model', 'Whisper model', 'select', { options: ['tiny','base','small','medium','large-v3'] }),
          field('whisper_device', 'Whisper device', 'select', { options: ['auto','cpu','cuda'], hint: 'Auto использует CUDA только если CTranslate2 реально её видит.' }),
          field('whisper_compute', 'Whisper compute', 'select', { options: ['auto','int8','int8_float16','int8_float32','float16','float32'] }),
          field('language', 'Language'),
          field('block_seconds', 'Block seconds', 'number', { min: 30, max: 1800 }),
          field('chunk_seconds', 'Chunk seconds', 'number', { min: 60, max: 3600 }),
          field('micro_cut_enabled', 'Micro-cut', 'bool'),
          field('micro_window_seconds', 'Micro window', 'number', { min: 10, max: 300 }),
          field('micro_min_seconds', 'Micro min', 'number', { min: 3, max: 120 }),
          field('micro_max_seconds', 'Micro max', 'number', { min: 10, max: 600 }),
          field('top_blocks_for_micro', 'Top blocks', 'number', { min: 1, max: 200 }),
          field('min_final_segments', 'Min final segments', 'number', { min: 1, max: 300 }),
          field('max_final_segments', 'Max final segments', 'number', { min: 1, max: 500 }),
        ]
      },
      visual: {
        title: 'Visual / OCR',
        description: 'Кадры, чат, донаты и экранные события. Чем выше режим, тем дольше обработка.',
        fields: [
          field('visual_mode', 'Visual mode', 'select', { options: ['Выкл','Лёгкий','Средний','Полный'] }),
          field('visual_scan_enabled', 'Visual Scan', 'bool'),
          field('visual_scan_interval_seconds', 'Visual interval', 'number', { min: 1, max: 120 }),
          field('visual_scan_max_samples', 'Max visual samples', 'number', { min: 1, max: 10000 }),
          field('ocr_enabled', 'OCR', 'bool'),
          field('ocr_languages', 'OCR languages'),
          field('ocr_every_n_visual_samples', 'OCR every N samples', 'number', { min: 1, max: 20 }),
          field('ocr_roi_enabled', 'OCR ROI', 'bool'),
          field('ocr_roi_x', 'ROI X', 'number', { min: 0, max: 1, step: 0.01 }),
          field('ocr_roi_y', 'ROI Y', 'number', { min: 0, max: 1, step: 0.01 }),
          field('ocr_roi_w', 'ROI W', 'number', { min: 0, max: 1, step: 0.01 }),
          field('ocr_roi_h', 'ROI H', 'number', { min: 0, max: 1, step: 0.01 }),
          field('ocr_upscale', 'OCR upscale', 'number', { min: 1, max: 6 }),
          field('semantic_quality_guard_enabled', 'Фильтр reconnect/replay', 'bool', { hint: 'Отсекает waiting, reconnect, intermission, replay, prerecorded и рекламу по смыслу сцены/OCR.' }),
          field('non_primary_reject_confidence', 'Non-primary reject confidence', 'number', { min: 0.5, max: 1, step: 0.01 }),
        ]
      },
      twitch: {
        title: 'Twitch / Preview',
        description: 'Загрузка VOD/Live, cookies, формат и preview-resolution.',
        fields: [
          field('twitch_download_timeout', 'Download timeout', 'number', { min: 300, max: 86400 }),
          field('twitch_download_threads', 'Download threads', 'number', { min: 1, max: 64 }),
          field('twitch_cookies_browser', 'Cookies browser', 'select', { options: ['none','chrome','firefox','edge','brave','opera'] }),
          field('twitch_format', 'yt-dlp format'),
          field('preview_resolution', 'Preview resolution', 'select', { options: ['360p','480p','720p','1080p'] }),
        ]
      },
      render: {
        title: 'Монтаж / Render',
        description: 'Финальная сборка, CRF, encoder, storyline/dedup и fill-control.',
        fields: [
          field('video_encoder', 'Video encoder', 'select', { options: ['auto','libx264','h264_nvenc','hevc_nvenc'], hint: 'Auto сначала выполняет реальный NVENC test, затем выбирает encoder.' }),
          field('render_preset', 'Render preset', 'select', { options: ['ultrafast','superfast','veryfast','faster','fast','medium','slow'] }),
          field('crf', 'CRF', 'number', { min: 14, max: 35 }),
          field('dedup_enabled', 'Dedup', 'bool'),
          field('storyline_enabled', 'Storyline', 'bool'),
          field('hook_min_score', 'Hook min score', 'number', { min: 0, max: 10, step: 0.1 }),
          field('audio_dynamics_enabled', 'Audio dynamics', 'bool'),
          field('refill_after_dedup_enabled', 'Refill after dedup', 'bool'),
          field('strict_quality_mode', 'Strict quality mode', 'bool'),
          field('strict_quality_min_score', 'Strict min score', 'number', { min: 0, max: 10, step: 0.1 }),
          field('strict_quality_min_confidence', 'Strict min confidence', 'number', { min: 0, max: 10, step: 0.1 }),
          field('quality_first_selection_enabled', 'Качество важнее длительности', 'bool', { hint: 'Не добавляет слабые/технические сцены только ради достижения заданных минут.' }),
          field('quality_first_min_score', 'Quality-first min score', 'number', { min: 0, max: 10, step: 0.1 }),
          field('quality_first_min_confidence', 'Quality-first min confidence', 'number', { min: 0, max: 10, step: 0.1 }),
          field('quality_first_min_clarity', 'Min standalone clarity', 'number', { min: 0, max: 1, step: 0.05 }),
          field('target_fill_ratio', 'Target fill ratio', 'number', { min: 0.1, max: 1, step: 0.01 }),
          field('remove_silence', 'Remove silence', 'bool'),
          field('batch_export_minutes', 'Batch exports'),
        ]
      },
      metadata: {
        title: 'Metadata / Shorts',
        description: 'YouTube-пакет, субтитры, Shorts и AI metadata.',
        fields: [
          field('generate_metadata', 'Generate metadata', 'bool'),
          field('metadata_ai_enabled', 'Metadata AI', 'bool'),
          field('require_ai_metadata', 'Require AI metadata', 'bool'),
          field('metadata_timeout', 'Metadata timeout', 'number', { min: 30, max: 7200 }),
          field('metadata_ai_retries', 'Metadata retries', 'number', { min: 0, max: 20 }),
          field('metadata_max_segments', 'Metadata clips', 'number', { min: 1, max: 100 }),
          field('make_srt', 'Создать общий SRT', 'bool'),
          field('shorts_count', 'Количество Shorts', 'number', { min: 1, max: 50 }),
          field('shorts_vertical_reframe', 'Вертикальный формат 9:16', 'bool'),
          field('shorts_reframe_mode', 'Вертикальная компоновка', 'select', { options: [['auto','Auto — умный безопасный reframe'],['smart_face','Smart Face'],['gameplay_facecam','Gameplay + Facecam'],['smart_zoom','Увеличенный центр + размытый фон (без слежения)'],['blur_background','Полный кадр + размытый фон'],['center_crop','Центральный crop'],['fit','Полный кадр с полями']] }),
          field('shorts_caption_quality', 'Качество субтитров Shorts', 'select', { options: [['fast','Быстро'],['high','Высоко'],['max','Максимум']] }),
          field('shorts_caption_font_size', 'Размер субтитров Shorts', 'number', { min: 48, max: 96 }),
          field('shorts_burn_subtitles', 'Вшивать субтитры в Shorts', 'bool'),
          field('shorts_dynamic_captions', 'Динамические субтитры с подсветкой слов', 'bool'),
          field('shorts_hook_title_enabled', 'Заголовок-hook в первые секунды', 'bool'),
          field('shorts_trim_silence', 'Подрезать длинные паузы по краям', 'bool'),
          field('shorts_caption_max_words', 'Слов в одной фразе субтитров', 'number', { min: 3, max: 8, step: 1 }),
          field('shorts_normalize_audio', 'Выравнивать громкость Shorts', 'bool'),
          field('shorts_min_seconds', 'Минимальная длительность Shorts', 'number', { min: 1, max: 30, step: 1 }),
          field('shorts_max_seconds', 'Максимальная длительность Shorts', 'number', { min: 5, max: 180, step: 5 }),
          field('shorts_crf', 'Качество Shorts (CRF)', 'number', { min: 15, max: 35 }),
        ]
      },
      prompt: {
        title: 'Prompt',
        description: 'Главная инструкция для AI-отбора. Это реально влияет на качество нарезки.',
        fields: [field('prompt', 'AI Prompt', 'textarea')]
      },
    }
    const tabs = [['main','Основные'],['ai','AI'],['video','Распознавание'],['visual','Изображение и чат'],['twitch','Twitch'],['render','Экспорт'],['metadata','Публикация'],['prompt','Инструкция AI']]
    const group=settingsGroups[settingsTab]||settingsGroups.main
    const visibleFields=group.fields.filter(Boolean)
    const stored=project?.settings||loadSafeLocalSettings()
    const dirty=JSON.stringify(settings)!==JSON.stringify({...settings,...stored})
    const stableFields=[
      field('content_type','Тип контента','select',{options:[['Auto','Автоматически'],['IRL стрим','IRL-стрим'],['IRL прогулка/город','IRL: прогулка или город'],['Игры','Игры'],['Спорт','Спорт'],['Подкаст','Подкаст'],['Shorts','Shorts']]}),
      field('edit_mode','Стиль монтажа','select',{options:[['Сбалансированный','Сбалансированный'],['Плотно','Динамичный'],['С историей','С сохранением истории'],['Только смешное','Юмористические моменты'],['Только конфликт/реакции','Конфликты и реакции'],['IRL конфликт/хаос','Динамичный IRL']]}),
      field('target_minutes','Ориентир по длительности, минут','number',{min:1,max:240,hint:'Если сильных моментов меньше, ролик будет короче — слабые фрагменты не добавляются только ради длительности.'}),
      field('analysis_profile','Приоритет','select',{options:[['fast','Быстрее'],['balanced','Баланс'],['quality','Лучшее качество']]}),
      field('make_srt','Создать субтитры','bool'),
    ].filter(Boolean)
    return <div className="uPage settingsPage"><StepHeader eyebrow="Настройки" title={productMode==='pro'?'Расширенные настройки':'Основные настройки'} description={productMode==='pro'?'Полный контроль над AI, распознаванием, Twitch и рендером.':'Только параметры, которые действительно нужны для большинства проектов.'}/>
      <section className="uCard settingsControlCard"><div className="uCardHead"><div><span>Режим интерфейса</span><h3>{productMode==='pro'?'Расширенный':'Основной'}</h3><p>{productMode==='pro'?'Показывает все технические параметры.':'Скрывает технические детали и оставляет безопасные настройки.'}</p></div><button onClick={()=>setMode(productMode==='pro'?'stable':'pro')}>{productMode==='pro'?'Вернуться в основной режим':'Открыть расширенный режим'}</button></div><div className={`settingsSaveState ${dirty?'dirty':'saved'}`}>{dirty?'Есть несохранённые изменения':'Настройки сохранены'}</div><div className="themePicker" role="radiogroup" aria-label="Цветовая тема">{THEME_OPTIONS.map(theme => <button type="button" role="radio" aria-checked={uiTheme === theme.id} key={theme.id} data-theme-choice={theme.id} className={`themeChoice ${uiTheme === theme.id ? 'selected' : ''}`} onClick={() => selectTheme(theme.id)}><span className="themeSwatch" aria-hidden="true"/><span><b>{theme.label}</b><small>{theme.description}</small></span></button>)}</div></section>
      {productMode==='stable'?<section className="uCard settingsEditorCard"><div className="uCardHead"><div><span>Главное</span><h3>Настройки проекта</h3><p>Остальное приложение подберёт автоматически.</p></div></div><div className="uFormGrid settingsGridClean">{stableFields}</div></section>:<><section className="uCard settingsControlCard"><div className="settingsTopLine"><input value={settingsQuery} onChange={e=>setSettingsQuery(e.target.value)} placeholder="Поиск по всем техническим настройкам"/><div className="settingsStatePill">{project?project.name:'Локальный профиль'}</div></div><div className="settingsTabs" role="tablist">{tabs.map(([id,label])=><button role="tab" aria-selected={settingsTab===id} key={id} className={settingsTab===id?'active':''} onClick={()=>setSettingsTab(id)}>{label}</button>)}</div></section><section className="uCard settingsEditorCard"><div className="uCardHead"><div><span>Расширенные</span><h3>{group.title}</h3><p>{group.description}</p></div><div className="settingsCount">{visibleFields.length} параметров</div></div><div className="uFormGrid settingsGridClean">{visibleFields.length?visibleFields:<p className="muted wide">Ничего не найдено.</p>}</div></section>{renderStableProfileCard()}</>}
      {authStatus?.accounts_enabled && <AccountAccessPanel user={currentUser} onLoggedOut={() => { setCurrentUser(null); setAuthStatus(st => ({...(st||{}), authenticated:false, user:null})) }}/>}
      {authStatus?.accounts_enabled && projectAccess?.can_manage && <TeamAccessPanel project={project} />}
      {!authStatus?.accounts_enabled && <PaidBetaPanel initialLicense={licenseStatus} onLicenseChange={setLicenseStatus} />}
      {!authStatus?.accounts_enabled && <MassReleasePanel productMode={productMode} onCreateSupportBundle={createSupportBundle} />}
      {!authStatus?.accounts_enabled && renderUpdateCard()}
      <section className="uCard settingsActionsCard"><div className="primaryActionRow">{(!authStatus?.accounts_enabled || currentUser?.global_role === 'admin') && <button onClick={()=>setFirstRunOpen(true)}>Открыть мастер настройки</button>}{(!authStatus?.accounts_enabled || currentUser?.global_role === 'admin') && <button onClick={createSupportBundle} disabled={supportBundleBusy}>{supportBundleBusy?'Создаю отчёт…':'Создать отчёт поддержки'}</button>}<button onClick={()=>{clearLocalSettings();setError('Локальный профиль настроек очищен.')}}>Сбросить локальный профиль</button><button className="primaryStrong" onClick={async()=>{try{await saveSettings()}catch(e){notifyError('Настройки не сохранились',e)}}}>Сохранить настройки</button></div></section>
    </div>
  }


  function renderActiveStep() {
    if (activeStep === 'projects') return renderProjectsHome()
    const requestedWorkflowStep = workflowSteps.find(step => step.id === activeStep)
    if (requestedWorkflowStep?.locked) {
      if (!sourceReady && ['style', 'analysis', 'review', 'export'].includes(activeStep)) {
        return renderUnifiedImportStep()
      }
      const previousStep = workflowSteps[Math.max(0, workflowSteps.findIndex(step => step.id === requestedWorkflowStep.id) - 1)]
      return <div className="uPage"><section className="blockedStepCard"><Clock3 size={24}/><span>Этап пока недоступен</span><h2>{requestedWorkflowStep.title}</h2><p>{requestedWorkflowStep.blockedReason}</p><button className="primaryStrong" onClick={() => openWorkflowStep(previousStep)}>Вернуться: {previousStep.title}</button></section></div>
    }
    if (activeStep === 'import') return renderUnifiedImportStep()
    if (activeStep === 'style') return renderUnifiedStyleStep()
    if (activeStep === 'analysis') return renderUnifiedAnalysisStep()
    if (activeStep === 'review') return renderUnifiedReviewStep()
    if (activeStep === 'export') return renderUnifiedExportStep()
    if (activeStep === 'reports') return renderUnifiedReportsStep()
    if (activeStep === 'settings') return renderUnifiedSettingsStep()
    return renderUnifiedImportStep()
  }

  if (appBooting && authStatus === null) return <div className="appBootScreen" data-theme={uiTheme}><div className="bootMark"><Scissors size={25}/></div><div><b>Highlight Studio</b><span>Проверяем готовность приложения…</span></div><div className="bootProgress" aria-label="Загрузка приложения"><i/></div></div>
  if (authStatus?.accounts_enabled && !currentUser) return <AuthScreen status={authStatus} onAuthenticated={user => { setCurrentUser(user); setAuthStatus(st => ({...(st||{}),authenticated:true,user})); initApp() }}/>
  return <div className={`studioApp studioAppV2 density-${uiDensity} ${sidebarCompact ? 'sidebarIsCompact' : ''}`} data-theme={uiTheme} data-release="11.2.7" data-design="studio-audited-v15" data-sidebar-state={sidebarCompact ? 'compact' : 'expanded'} data-density={uiDensity}>
    <FirstRunWizard
      open={firstRunOpen && (!authStatus?.accounts_enabled || currentUser?.global_role === 'admin')}
      systemCheck={systemCheck}
      systemCheckLoading={systemCheckLoading}
      desktopInfo={desktopInfo}
      onRunSystemCheck={runSystemCheck}
      onComplete={payload => { setOnboarding(payload); setFirstRunOpen(false); setActiveStep('projects'); setNotice({ type: 'success', title: 'Настройка завершена', message: 'Теперь можно открыть первое видео.', action: { label: 'Открыть видео', id: 'open-source' } }) }}
      onClose={onboarding?.completed ? () => setFirstRunOpen(false) : undefined}
    />
    {renderProductSidebar()}
    <div className="studioStage">
      {renderTopBar()}
      {renderJobBar()}
      <div className="stageNotices">
        {projectAccess && !projectAccess.can_edit && <div role="status" className="noticeBanner info"><span>Режим просмотра: ты можешь смотреть проект и скачивать результаты, но не менять монтаж.</span><button onClick={()=>setActiveStep('settings')}>Права доступа</button></div>}
        {notice && <div role={notice.type === 'error' ? 'alert' : 'status'} aria-live="polite" className={`noticeBanner structuredNotice ${notice.type}`}><span className="noticeCopy"><b>{notice.title}</b><span>{notice.message}</span></span><span className="noticeActions">{notice.action && <button className="noticeAction" onClick={() => handleNoticeAction(notice.action.id)}>{notice.action.label}</button>}<button className="noticeClose" aria-label="Закрыть уведомление" onClick={() => setNoticeState(null)}><X size={16}/></button></span></div>}
      </div>
      <main className={`studioLayoutV2 ${['projects', 'reports', 'settings'].includes(activeStep) ? 'utilityLayout' : 'workflowLayout'} ${activeStep === 'review' ? 'editorLayout' : ''}`}>
        <section className="studioWorkspace">{renderActiveStep()}</section>
      </main>
    </div>
  </div>
}
