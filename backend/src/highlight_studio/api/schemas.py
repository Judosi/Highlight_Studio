"""Pydantic contracts for the HTTP API.

Keeping request/settings schemas out of ``app.py`` makes the transport layer
smaller and prevents route orchestration from becoming the owner of validation
rules.  The classes are re-exported by ``api.app`` for backward compatibility.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

class AppSettings(BaseModel):
    """Validated project settings. Extra keys are preserved for forward compatibility."""

    model_config = ConfigDict(extra="allow")

    ai_engine: str = Field("ollama", max_length=30)
    ollama_url: str = Field("http://localhost:11434", min_length=1, max_length=300)
    ollama_timeout: int = Field(900, ge=30, le=7200)
    ollama_keep_alive: str = Field("5m", max_length=20)
    ollama_num_ctx: int = Field(4096, ge=2048, le=16384)
    hardware_profile: str = Field("Auto", max_length=160)
    hardware_detected_profile: str = Field("", max_length=240)
    hardware_auto_optimize: bool = True
    hardware_quality_guard_enabled: bool = True
    hardware_manual_override_enabled: bool = False
    hardware_runtime_policy_version: int = Field(2, ge=1, le=10)
    cpu_worker_limit: int = Field(0, ge=0, le=64)
    gpu_job_limit: int = Field(1, ge=1, le=4)
    gpu_vram_reserve_mb: int = Field(768, ge=0, le=8192)
    hardware_decode: str = Field("auto", max_length=20)
    analysis_profile: str = Field("balanced", max_length=40)
    task_preset: str = Field("balanced", max_length=60)
    task_preset_label: str = Field("Сбалансированный / смысл", max_length=120)
    openai_api_key: str = Field("", max_length=600)
    openai_base_url: str = Field("https://api.openai.com/v1", max_length=400)
    openai_text_model: str = Field("gpt-4o-mini", max_length=160)
    openai_vision_model: str = Field("gpt-4o-mini", max_length=160)
    openai_timeout: int = Field(900, ge=30, le=7200)
    openai_temperature: float = Field(0.15, ge=0.0, le=1.0)
    openai_privacy_mode: str = Field("transcript_only", max_length=40)
    openai_max_images: int = Field(6, ge=1, le=12)
    openai_compatible_mode: bool = False
    hybrid_disable_fallback: bool = False
    ai_batch_size: int = Field(4, ge=1, le=12)
    ai_retry_count: int = Field(2, ge=1, le=10)
    ai_transport_retry_count: int = Field(2, ge=1, le=3)
    ai_request_hard_timeout: int = Field(360, ge=90, le=1800)
    ai_request_active_hard_timeout: int = Field(900, ge=90, le=3600)
    ollama_think: bool = False
    ai_ttft_timeout: int = Field(180, ge=30, le=300)
    ai_stream_stall_timeout: int = Field(60, ge=10, le=180)
    ai_circuit_failure_threshold: int = Field(2, ge=1, le=10)
    ai_circuit_cooldown_seconds: int = Field(60, ge=1, le=600)
    ai_batch_completeness_required: bool = True
    ai_strict_mode: bool = False
    full_ai_coverage: bool = False
    micro_batch_size: int = Field(8, ge=1, le=20)
    text_model: str = Field("qwen3:8b", min_length=1, max_length=120)
    vision_model: str = Field("qwen3-vl:8b", min_length=1, max_length=120)
    whisper_model: str = Field("small", min_length=1, max_length=40)
    whisper_device: str = Field("auto", min_length=2, max_length=20)
    whisper_compute: str = Field("auto", min_length=3, max_length=20)
    language: str = Field("ru", min_length=2, max_length=12)

    block_seconds: int = Field(180, ge=30, le=1800)
    chunk_seconds: int = Field(900, ge=60, le=7200)
    micro_cut_enabled: bool = True
    micro_window_seconds: int = Field(45, ge=5, le=300)
    micro_min_seconds: int = Field(20, ge=3, le=300)
    micro_max_seconds: int = Field(75, ge=5, le=600)
    micro_speech_gap_seconds: float = Field(4.0, ge=1.0, le=15.0)
    micro_source_duration_multiplier: float = Field(4.0, ge=2.0, le=8.0)
    min_final_segments: int = Field(8, ge=1, le=500)
    max_final_segments: int = Field(80, ge=1, le=1000)
    top_blocks_for_micro: int = Field(24, ge=1, le=300)
    target_minutes: float = Field(30, ge=1, le=240)

    visual_mode: str = Field("Лёгкий", max_length=40)
    prompt: str = Field(
        "Собери плотную интересную нарезку. Оставляй юмор, эмоции, конфликт, донаты, чат, мемы, личные истории. Убирай технику, ожидание, воду и повторы.",
        max_length=12000,
    )
    strict_preflight: bool = False
    video_encoder: str = Field("auto", max_length=80)
    render_preset: str = Field("veryfast", max_length=40)
    crf: int = Field(23, ge=15, le=35)
    batch_export_minutes: str = Field("10,30,60", max_length=120)
    shorts_count: int = Field(5, ge=1, le=50)
    shorts_min_seconds: float = Field(3.0, ge=1.0, le=30.0)
    shorts_max_seconds: float = Field(45.0, ge=5.0, le=180.0)
    shorts_reframe_mode: str = Field("auto", max_length=40)
    shorts_burn_subtitles: bool = True
    shorts_dynamic_captions: bool = True
    shorts_hook_title_enabled: bool = True
    shorts_trim_silence: bool = True
    shorts_caption_max_words: int = Field(4, ge=3, le=8)
    shorts_caption_quality: str = Field("high", max_length=20)
    shorts_caption_font_size: int = Field(72, ge=48, le=96)
    shorts_hook_font_size: int = Field(82, ge=56, le=108)
    shorts_caption_outline: int = Field(5, ge=3, le=8)
    shorts_precise_alignment: bool = True
    shorts_whisper_model: str = Field("small", max_length=40)
    shorts_recognition_dictionary: str = Field("", max_length=2000)
    shorts_funny_search_enabled: bool = True
    shorts_emotion_events_enabled: bool = True
    shorts_sensevoice_model: str = Field("iic/SenseVoiceSmall", max_length=160)
    shorts_emotion_top_n: int = Field(20, ge=1, le=40)
    shorts_face_sample_fps: float = Field(1.0, ge=0.35, le=2.0)
    shorts_llm_rerank_enabled: bool = False
    shorts_llm_rerank_top: int = Field(10, ge=3, le=20)
    shorts_candidate_pool_limit: int = Field(48, ge=10, le=120)
    shorts_min_quality_score: float = Field(4.8, ge=0.0, le=10.0)
    shorts_normalize_audio: bool = True
    shorts_render_preset: str = Field("veryfast", max_length=40)
    shorts_crf: int = Field(22, ge=15, le=35)

    dedup_enabled: bool = True
    storyline_enabled: bool = True
    make_srt: bool = True
    generate_metadata: bool = True
    metadata_ai_enabled: bool = False
    require_ai_metadata: bool = False
    metadata_timeout: int = Field(300, ge=15, le=7200)
    metadata_ai_retries: int = Field(5, ge=1, le=10)
    metadata_max_segments: int = Field(12, ge=1, le=300)
    remove_silence: bool = False
    audio_dynamics_enabled: bool = True
    refill_after_dedup_enabled: bool = True
    target_fill_ratio: float = Field(0.94, ge=0.5, le=1.0)
    strict_quality_mode: bool = False
    strict_quality_min_score: float = Field(7.2, ge=0.0, le=10.0)
    strict_quality_min_confidence: float = Field(6.2, ge=0.0, le=10.0)
    # v10.15.9 quality-first semantics.  These settings do not lower model or
    # sampling quality; they prevent technical/replay material and weak refill
    # from occupying the requested target duration.
    semantic_quality_guard_enabled: bool = True
    non_primary_reject_confidence: float = Field(0.72, ge=0.5, le=1.0)
    quality_first_selection_enabled: bool = True
    quality_first_min_score: float = Field(6.7, ge=0.0, le=10.0)
    quality_first_min_confidence: float = Field(5.6, ge=0.0, le=10.0)
    quality_first_min_clarity: float = Field(0.5, ge=0.0, le=1.0)
    quality_recovery_score_relaxation: float = Field(0.5, ge=0.0, le=1.0)
    temporal_fairness_enabled: bool = True
    temporal_fairness_bucket_seconds: int = Field(900, ge=300, le=3600)
    temporal_fairness_blocks_per_bucket: int = Field(2, ge=1, le=5)
    temporal_fairness_min_score: float = Field(4.5, ge=0.0, le=10.0)
    micro_global_score_floor: float = Field(7.6, ge=0.0, le=10.0)

    auto_mode: str = Field("Auto", max_length=40)
    content_type: str = Field("Auto", max_length=80)
    auto_target_duration: bool = True
    preview_resolution: str = Field("720p", max_length=20)
    shorts_vertical_reframe: bool = True
    twitch_download_timeout: int = Field(43200, ge=300, le=172800)
    twitch_download_threads: int = Field(16, ge=1, le=64)
    twitch_cookies_browser: str = Field("none", max_length=40)
    twitch_format: str = Field("best", max_length=120)
    twitch_download_engine: str = Field("auto", max_length=60)
    twitch_quality: str = Field("best", max_length=80)
    twitch_speed_test_seconds: int = Field(45, ge=10, le=180)
    twitch_fallback_enabled: bool = True
    twitch_aria2_connections: int = Field(16, ge=1, le=64)
    twitch_downloader_cli_path: str = Field("", max_length=1200)
    edit_mode: str = Field("Сбалансированный", max_length=60)
    visual_scan_enabled: bool = True
    visual_scan_interval_seconds: int = Field(5, ge=3, le=60)
    visual_scan_max_samples: int = Field(1200, ge=20, le=10000)
    ocr_enabled: bool = True
    ocr_languages: str = Field("rus+eng", min_length=3, max_length=40)
    ocr_every_n_visual_samples: int = Field(2, ge=1, le=20)
    ocr_roi_enabled: bool = False
    ocr_roi_x: float = Field(0.0, ge=0.0, le=1.0)
    ocr_roi_y: float = Field(0.0, ge=0.0, le=1.0)
    ocr_roi_w: float = Field(1.0, ge=0.05, le=1.0)
    ocr_roi_h: float = Field(1.0, ge=0.05, le=1.0)
    ocr_upscale: int = Field(2, ge=1, le=4)
    hook_first_enabled: bool = False
    hook_min_score: float = Field(8.7, ge=6.0, le=10.0)

    @model_validator(mode="after")
    def validate_consistency(self):
        if self.micro_max_seconds < self.micro_min_seconds:
            raise ValueError("micro_max_seconds must be greater than or equal to micro_min_seconds")
        if self.micro_window_seconds < self.micro_min_seconds:
            raise ValueError("micro_window_seconds must be greater than or equal to micro_min_seconds")
        if self.max_final_segments < self.min_final_segments:
            raise ValueError("max_final_segments must be greater than or equal to min_final_segments")
        allowed_visual = {"Выкл", "Лёгкий", "Средний", "Полный"}
        if self.visual_mode not in allowed_visual:
            raise ValueError(f"visual_mode must be one of: {', '.join(sorted(allowed_visual))}")
        allowed_auto = {"Auto", "Быстро", "Качество", "Fast", "Quality", "Manual"}
        if self.auto_mode not in allowed_auto:
            raise ValueError(f"auto_mode must be one of: {', '.join(sorted(allowed_auto))}")
        allowed_content = {
            "Auto",
            "Стрим",
            "Обычный стрим",
            "IRL стрим",
            "IRL прогулка/город",
            "Игры",
            "Игровой стрим",
            "Спорт",
            "Реакции",
            "Подкаст",
            "Подкаст/интервью",
            "Интервью",
            "Лекция",
            "Shorts",
        }
        if self.content_type not in allowed_content:
            raise ValueError(f"content_type must be one of: {', '.join(sorted(allowed_content))}")
        allowed_edit = {
            "Сбалансированный",
            "Плотно",
            "С историей",
            "Только смешное",
            "Только конфликт/реакции",
            "IRL плотный",
            "IRL история",
            "IRL конфликт/хаос",
        }
        if self.edit_mode not in allowed_edit:
            raise ValueError(f"edit_mode must be one of: {', '.join(sorted(allowed_edit))}")
        allowed_hw = {"Auto", "GTX1050Ti_16GB_Ryzen2600", "LowVRAM", "CPU_Stable"}
        if self.hardware_profile not in allowed_hw:
            raise ValueError(f"hardware_profile must be one of: {', '.join(sorted(allowed_hw))}")
        self.whisper_device = str(self.whisper_device or "auto").strip().lower()
        if self.whisper_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("whisper_device must be one of: auto, cpu, cuda")
        self.whisper_compute = str(self.whisper_compute or "auto").strip().lower()
        if self.whisper_compute not in {"auto", "int8", "int8_float16", "int8_float32", "float16", "float32"}:
            raise ValueError("whisper_compute must be one of: auto, int8, int8_float16, int8_float32, float16, float32")
        self.video_encoder = str(self.video_encoder or "auto").strip().lower()
        if self.video_encoder not in {"auto", "libx264", "h264_nvenc", "hevc_nvenc"}:
            raise ValueError("video_encoder must be one of: auto, libx264, h264_nvenc, hevc_nvenc")
        self.hardware_decode = str(self.hardware_decode or "off").strip().lower()
        if self.hardware_decode not in {"off", "auto"}:
            raise ValueError("hardware_decode must be one of: off, auto")
        allowed_cookies = {"none", "firefox", "chrome", "edge", "brave", "opera", "vivaldi", "safari"}
        self.twitch_cookies_browser = (self.twitch_cookies_browser or "none").strip().lower()
        if self.twitch_cookies_browser not in allowed_cookies:
            raise ValueError(f"twitch_cookies_browser must be one of: {', '.join(sorted(allowed_cookies))}")
        self.twitch_format = (self.twitch_format or "best").strip() or "best"
        allowed_twitch_engines = {"auto", "twitchdownloadercli", "yt-dlp", "yt-dlp-aria2c", "streamlink", "manual-twitchlink"}
        self.twitch_download_engine = (self.twitch_download_engine or "auto").strip().lower()
        if self.twitch_download_engine not in allowed_twitch_engines:
            raise ValueError(f"twitch_download_engine must be one of: {', '.join(sorted(allowed_twitch_engines))}")
        self.twitch_quality = (self.twitch_quality or "best").strip() or "best"
        if any(ch in self.twitch_quality for ch in "\r\n;`|&"):
            self.twitch_quality = "best"
        self.twitch_downloader_cli_path = (self.twitch_downloader_cli_path or "").strip().strip('"').strip("'")
        # v10.15.4 briefly exposed an unsupported `safe` value in the UI.
        # Migrate those saved projects instead of rejecting settings before the
        # analysis POST can even be sent.
        if self.analysis_profile == "safe":
            self.analysis_profile = "fast"
        allowed_analysis = {"fast", "balanced", "quality"}
        if self.analysis_profile not in allowed_analysis:
            raise ValueError(f"analysis_profile must be one of: {', '.join(sorted(allowed_analysis))}")
        keep_alive = str(self.ollama_keep_alive or "5m").strip().lower()
        if keep_alive not in {"0", "30s", "1m", "5m", "10m", "30m"}:
            raise ValueError("ollama_keep_alive must be one of: 0, 30s, 1m, 5m, 10m, 30m")
        self.ollama_keep_alive = keep_alive
        # v9.1.9: cloud API engines were removed from the user-facing product.
        # Old projects that had gemini/openai/hybrid are migrated back to local Ollama.
        self.ai_engine = "ollama"

        # UX-safety: if the user picked an IRL A/B mode from advanced settings
        # but left Content type on Auto, enable the full IRL profile anyway.
        # Otherwise only the edit-mode prompt would be IRL, while adaptive target,
        # content prompt and IRL scoring bonuses would still behave like Auto/stream.
        if self.edit_mode.startswith("IRL") and self.content_type == "Auto":
            self.content_type = "IRL стрим"
        return self


class OnboardingCompleteRequest(BaseModel):
    ai_mode: str = Field(default="local", pattern="^(local|cloud_later)$")
    telemetry_enabled: bool = False
    accepted_privacy: bool = False
    accepted_terms: bool = False


class LicenseActivateRequest(BaseModel):
    key: str = Field(..., min_length=8, max_length=8000)


class PrivacyPreferencesRequest(BaseModel):
    telemetry_enabled: bool = False
    crash_reports_enabled: bool = False
    accepted_privacy: bool = False
    accepted_terms: bool = False


class BetaFeedbackRequest(BaseModel):
    category: str = Field("other", pattern="^(quality|bug|speed|usability|idea|other)$")
    rating: int = Field(5, ge=1, le=5)
    message: str = Field(..., min_length=3, max_length=2000)
    allow_contact: bool = False
    email: str = Field("", max_length=320)


class ReleaseEvidenceRequest(BaseModel):
    windows_devices_tested: int = Field(0, ge=0, le=100000)
    production_sessions: int = Field(0, ge=0, le=100000000)
    crashed_sessions: int = Field(0, ge=0, le=100000000)
    artifacts_signed: bool = False
    installer_verified: bool = False
    update_verified: bool = False
    notes: str = Field("", max_length=2000)


class FrontendCrashRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    stack: str = Field("", max_length=8000)
    route: str = Field("", max_length=400)


class ClipPreviewRequest(BaseModel):
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    force: bool = False


class ShortRenderRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    hook_text: str = Field("", max_length=240)
    caption_text: str | None = Field(None, max_length=2000)
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    reframe_mode: str = Field("auto", max_length=40)

    @model_validator(mode="after")
    def validate_short(self):
        allowed = {"auto", "smart_face", "gameplay_facecam", "blur_background", "smart_zoom", "center_crop", "fit"}
        self.title = re.sub(r"\s+", " ", self.title).strip()
        if not self.title:
            raise ValueError("Введите название Short")
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("Начало и конец должны быть конечными числами")
        if self.end <= self.start:
            raise ValueError("Конец должен быть позже начала")
        if self.end - self.start < 1:
            raise ValueError("Длительность Short должна быть не меньше 1 секунды")
        if self.reframe_mode not in allowed:
            raise ValueError("Выберите доступный режим кадрирования")
        return self


class YouTubeUploadItem(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=1000)
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    privacy_status: str = Field("private", pattern="^(private|unlisted|public)$")
    category_id: str = Field("22", pattern=r"^\d{1,4}$")
    notify_subscribers: bool = False
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    publish_at: str = Field("", max_length=80)


class YouTubeUploadRequest(BaseModel):
    items: list[YouTubeUploadItem] = Field(..., min_length=1, max_length=50)


class ImportProjectRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=2000)
    storage_mode: str = Field("reference", pattern="^(reference|copy|link)$")
    name: str | None = Field(None, max_length=160)


class TwitchProjectRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    source_kind: str = Field("auto", pattern="^(auto|vod|live)$")
    vod_start: str | None = Field(None, max_length=40)
    vod_end: str | None = Field(None, max_length=40)
    live_record_minutes: int = Field(60, ge=1, le=720)
    twitch_download_threads: int = Field(16, ge=1, le=64)
    twitch_cookies_browser: str = Field("none", max_length=40)
    twitch_format: str = Field("best", max_length=120)
    twitch_download_engine: str = Field("auto", max_length=60)
    twitch_quality: str = Field("best", max_length=80)
    twitch_fallback_enabled: bool = True
    twitch_aria2_connections: int = Field(16, ge=1, le=64)
    name: str | None = Field(None, max_length=160)
    auto_start: bool = True
