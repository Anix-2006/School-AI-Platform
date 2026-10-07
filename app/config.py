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


settings = Settings()
