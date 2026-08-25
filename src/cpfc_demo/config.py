from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# This is the line to change live during the demo.
CHECKOUT_ENGINE: Literal["naive", "temporal"] = "temporal"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CPFC_", env_file=".env", extra="ignore")

    admin_token: str = "palace-admin-2026"
    internal_token: str = "palace-internal-2026"
    data_path: str = "./cpfc-demo.sqlite3"
    public_base_url: str = "http://localhost:8000"
    service_base_url: str = "http://localhost:8000"
    naive_worker_url: str = "http://localhost:8001"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "cpfc-ticket-orders"
    temporal_ui_url: str = "http://localhost:8233"
    checkout_engine_override: Literal["naive", "temporal"] | None = None
    stranded_after_seconds: float = 4.0
    default_seed: int = 1861

    @property
    def checkout_engine(self) -> Literal["naive", "temporal"]:
        return self.checkout_engine_override or CHECKOUT_ENGINE


@lru_cache
def get_settings() -> Settings:
    return Settings()
