"""Application configuration. All settings come from the environment."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- core ---
    app_name: str = "praxis"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://localhost:8080"

    # --- database ---
    database_url: str = "postgresql+psycopg://praxis_app:praxis_app_pw@localhost:5432/praxis"
    admin_database_url: str = (
        "postgresql+psycopg://praxis_owner:praxis_owner_pw@localhost:5432/praxis"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- auth ---
    jwt_secret: str = "dev-insecure-secret-change-me"
    jwt_secret_previous: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900          # 15 minutes
    refresh_token_ttl_seconds: int = 1209600     # 14 days
    password_min_length: int = 10
    argon2_time_cost: int = 2
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 2

    # --- rate limiting (in-process token bucket) ---
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_chat_per_minute: int = 60
    rate_limit_default_per_minute: int = 240

    # --- providers ---
    llm_provider: Literal["mock", "openai", "anthropic", "ollama"] = "mock"
    llm_model: str = "praxis-mock-1"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 60.0

    # --- neuro-symbolic enterprise assistant ---
    # "semantic" loads a real sentence-embedding model (ONNX, runs offline once
    # cached). "hashing" is the lexical fallback: it cannot understand paraphrase,
    # and every answer produced with it is flagged as degraded understanding.
    neural_encoder: Literal["semantic", "hashing"] = "semantic"
    neural_model: str = "BAAI/bge-small-en-v1.5"
    neural_model_cache: str = ""
    knowledge_pack_dir: str = ""
    knowledge_pack_tenant: str = "meridian"

    embedding_provider: Literal["hashing", "openai", "ollama"] = "hashing"
    embedding_model: str = "praxis-hash-384"
    embedding_dimension: int = 384

    # --- rag ---
    chunk_target_tokens: int = 220
    chunk_overlap_tokens: int = 40
    retrieval_top_k: int = 24
    rerank_top_k: int = 8
    hybrid_vector_weight: float = 0.55
    hybrid_lexical_weight: float = 0.45
    max_upload_bytes: int = 15728640

    # --- verification / gate ---
    evidence_relevance_threshold: float = 0.18
    claim_support_threshold: float = 0.40
    answer_confidence_threshold: float = 0.55
    caveat_confidence_threshold: float = 0.40
    max_context_messages: int = 20
    max_context_chars: int = 12000

    # --- observability ---
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("cors_origins")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
