import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY", description="Google Gemini API Key")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_MODEL", description="Gemini model name")
    llm_price_input: float = Field(default=0.075, description="Gemini price per 1M input tokens in USD")
    llm_price_output: float = Field(default=0.30, description="Gemini price per 1M output tokens in USD")

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/whatsapp_agent",
        description="Postgres connection string",
    )

    odoo_url: str = Field(default="http://localhost:8069", description="Odoo server URL")
    odoo_db: str = Field(default="odoo", description="Odoo database name")
    odoo_user: str = Field(default="admin", description="Odoo user email/username")
    odoo_api_key: str = Field(default="", description="Odoo user API key or password")

    wa_token: str = Field(default="", description="WhatsApp Graph API access token")
    wa_phone_number_id: str = Field(default="", description="WhatsApp Phone Number ID")
    wa_verify_token: str = Field(default="default_verify_token", description="Webhook verification token")
    wa_app_secret: str = Field(default="", description="WhatsApp App Secret for X-Hub-Signature-256")
    staff_alert_phone: str = Field(default="+923000000000", description="Staff phone number for alerts")

    admin_token: str = Field(default="admin_secret", description="Bearer token for admin API endpoints")

    business_name: str = Field(default="Al-Madina Mobile Store", description="Business name for prompt context")
    env: str = Field(default="development", description="Environment mode (development/production/testing)")
    log_level: str = Field(default="INFO", description="Logging level")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def validate_required_settings(self) -> None:
        """Fail fast in production if mandatory environment variables are missing."""
        if self.env == "testing":
            return
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.wa_token and self.env == "production":
            missing.append("WA_TOKEN")
        if not self.wa_app_secret and self.env == "production":
            missing.append("WA_APP_SECRET")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings()
