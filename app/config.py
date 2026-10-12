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
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    # Relative paths resolve from the repository root (/app in Docker).
    # Fill it once with `python -m app.voice.download_model`.
    whisper_model_dir: str = "models/whisper"
    voice_max_concurrency: int = 1
    voice_max_seconds: float = 30
    voice_max_bytes: int = 5_242_880
    tts_cache_size: int = 128
    voice_queue_timeout: float = 2.0
    voice_guardian_rate: int = 10
    voice_global_rate: int = 60
    voice_speak_rate: int = 20
    voice_tts_timeout: float = 4.0
    voice_audio_ttl: float = 300.0
    voice_vad_silence_ms: int = 800
    voice_idle_prompt_seconds: int = 20


settings = Settings()
