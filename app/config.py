from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    openai_model_fast: str = "gpt-4o-mini"
    openai_model_strong: str = "gpt-4o"

    database_url: str = "sqlite:///./dev.db"

    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""

    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"

    env: str = "development"
    default_tenant_id: str = "demo-school"

    class Config:
        env_file = ".env"


settings = Settings()
