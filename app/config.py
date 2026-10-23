from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Unknown keys (e.g. an old DATABASE_URL line in .env) are ignored.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str
    openai_model_fast: str = "gpt-4o-mini"
    openai_model_strong: str = "gpt-4o"
    # Below this classifier confidence the orchestrator asks the LLM router.
    intent_confidence_threshold: float = 0.6

    # The only data source: one workbook, one sheet per table.
    school_data_path: str = "data/school_data.xlsx"

    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""

    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"

    env: str = "development"
    # Empty means "first row in the workbook" (tenants / teachers sheet).
    default_tenant_id: str = ""
    default_teacher_id: str = ""

    voice_enabled: bool = False
    whisper_model: str = "tiny"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    voice_max_concurrency: int = 1
    voice_max_seconds: float = 30
    voice_max_bytes: int = 5_242_880
    tts_cache_size: int = 128
    # How many gTTS requests may run at once while a reply is spoken sentence by sentence.
    tts_max_parallel: int = 3
    # Pieces shorter than this are merged into the next sentence ("Ok." stays with what follows).
    tts_min_sentence_chars: int = 12
    voice_queue_timeout: float = 2.0
    voice_guardian_rate: int = 10
    voice_global_rate: int = 60
    voice_speak_rate: int = 20
    # Each gTTS attempt. One retry waits briefly, then the reply is text only.
    tts_timeout_seconds: float = 5.0
    tts_retries: int = 1
    tts_retry_delay_seconds: float = 0.2
    # After this many failed attempts in a row, skip speech instead of waiting again.
    tts_cooldown_failures: int = 3
    tts_cooldown_seconds: float = 30.0
    voice_audio_ttl: float = 300.0
    # Live conversation. Silence closes an utterance, VOICE_MAX_SECONDS caps it,
    # and sensitivity is the WebRTC VAD mode: 0 keeps quiet speech, 3 rejects more noise.
    voice_vad_silence_ms: int = 800
    voice_vad_sensitivity: int = 2
    voice_vad_frame_ms: int = 30
    voice_vad_preroll_ms: int = 300
    voice_vad_min_speech_ms: int = 300
    voice_vad_sample_rate: int = 16000
    voice_idle_prompt_seconds: int = 20


settings = Settings()
