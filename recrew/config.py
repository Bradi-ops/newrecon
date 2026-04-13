from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Target
    TARGET_URL: str = ""
    MAX_PAGES: int = 100
    MAX_DEPTH: int = 5

    # LLM
    LLM_PROVIDER: str = "openai"
    MODEL_NAME: str = "gpt-5.4-mini"
    OPENAI_API_KEY: str = "sk-placeholder"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # LM Studio
    LM_STUDIO_URL: str = "http://192.168.50.213:1234/v1"
    LM_STUDIO_MODEL: str = "qwen2.5-14b-instruct"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # Auth
    AUTH_ENABLED: bool = False
    AUTH_TYPE: str = "form"
    AUTH_URL: str = ""
    AUTH_USERNAME: str = ""
    AUTH_PASSWORD: str = ""
    AUTH_USERNAME_FIELD: str = "username"
    AUTH_PASSWORD_FIELD: str = "password"
    AUTH_COOKIES: str = "{}"
    AUTH_BEARER_TOKEN: str = ""

    # Playwright
    HEADLESS: bool = True
    BROWSER_TIMEOUT: int = 30000
    WAIT_AFTER_LOAD_MS: int = 1500

    # Output
    REPORT_DIR: str = "/app/reports"
    LOG_LEVEL: str = "INFO"


config = Config()