from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:////app/data/selin_tori.db"
    timezone: str = "Europe/Moscow"
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-luna"
    openai_daily_call_limit: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
