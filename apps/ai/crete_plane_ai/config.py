# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "crete-plane-ai"
    ai_database_url: SecretStr
    crete_ai_shared_secret: SecretStr = Field(min_length=32)
    azure_openai_endpoint: str
    azure_openai_api_key: SecretStr
    azure_openai_chat_deployment: str = "gpt-5.4-mini"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    ai_embedding_dimensions: int = 1536
    ai_hmac_max_age_seconds: int = Field(default=300, ge=1, le=300)
    ai_max_request_bytes: int = Field(default=524_288, ge=65_536, le=2_097_152)
    ai_database_pool_min_size: int = Field(default=1, ge=1, le=20)
    ai_database_pool_max_size: int = Field(default=10, ge=1, le=100)
    ai_azure_connect_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    ai_azure_read_timeout_seconds: float = Field(default=120.0, ge=5, le=600)
    ai_max_history_messages: int = Field(default=40, ge=1, le=100)
    ai_max_history_chars: int = Field(default=120_000, ge=1_000, le=500_000)
    ai_max_context_items: int = Field(default=30, ge=1, le=100)
    ai_max_context_chars: int = Field(default=160_000, ge=1_000, le=500_000)
    ai_max_embedding_input_chars: int = Field(default=24_000, ge=1_000, le=100_000)

    @field_validator("azure_openai_endpoint")
    @classmethod
    def validate_azure_endpoint(cls, value: str) -> str:
        endpoint = value.strip()
        if not endpoint.startswith("https://") or not endpoint.endswith("/openai/v1/"):
            raise ValueError("must be an HTTPS Azure OpenAI v1 endpoint ending in /openai/v1/")
        return endpoint

    @field_validator("ai_embedding_dimensions")
    @classmethod
    def fixed_embedding_dimensions(cls, value: int) -> int:
        if value != 1536:
            raise ValueError("only 1536-dimensional embeddings are supported")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
