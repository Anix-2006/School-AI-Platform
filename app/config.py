from pydantic import Field, model_validator
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
    # Parallel STT workers (CPU default 1). VOICE_MAX_CONCURRENCY is a legacy alias.
    voice_max_parallel: int = Field(1, ge=1)
    voice_max_concurrency: int = Field(1, ge=1)
    # Waiting jobs only (not counting active transcriptions).
    voice_queue_max: int = Field(16, ge=0)
    # Notify when waiting count after admission is >= this (waiting only).
    voice_queue_notify_at: int = Field(2, ge=1)
    voice_max_seconds: float = 30
    voice_max_bytes: int = 5_242_880
    tts_cache_size: int = 128
    # Retained for older .env files; admission uses voice_queue_max instead.
    voice_queue_timeout: float = 2.0
    voice_guardian_rate: int = 10
    voice_global_rate: int = 60
    voice_speak_rate: int = 20
    voice_tts_timeout: float = 4.0
    voice_audio_ttl: float = 300.0
    voice_vad_silence_ms: int = 800
    voice_idle_prompt_seconds: int = 20

    @model_validator(mode="after")
    def _alias_max_parallel(self) -> "Settings":
        """Honor legacy VOICE_MAX_CONCURRENCY when VOICE_MAX_PARALLEL is unset."""
        import os

        if (
            os.getenv("VOICE_MAX_PARALLEL") is None
            and os.getenv("VOICE_MAX_CONCURRENCY") is not None
        ):
            self.voice_max_parallel = self.voice_max_concurrency
        return self


settings = Settings()
