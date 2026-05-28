from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./storage/northwind.db"
    weaviate_url: str = "http://localhost:8080"

    # --- LLM: set LLM_PROVIDER=auto (default) + ONE API key below ---
    # Or force: openrouter | openai | anthropic | google | nvidia
    llm_provider: str = "auto"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "moonshotai/kimi-k2.6:free"
    openrouter_model_fallback: str = "deepseek/deepseek-v4-flash:free"
    openrouter_model_fallback_2: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_model_fallback: str = "gpt-4o"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-20241022"
    anthropic_model_fallback: str = "claude-3-5-sonnet-20241022"

    google_api_key: str = ""
    google_model: str = "gemini-2.5-flash"
    google_model_fallback: str = "gemini-3.5-flash"

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nvidia-nemotron-nano-9b-v2"
    nvidia_model_fallback: str = "meta/llama-3.1-8b-instruct"

    # Policy Q&A: local CPU embeddings (same for all LLM providers; no API key)
    local_embedding_model: str = "all-MiniLM-L6-v2"

    # Unstructured.io
    unstructured_api_key: str = ""
    unstructured_api_url: str = "https://api.unstructuredapp.io"

    # Policy RAG (hybrid = BM25 + vector in Weaviate)
    policy_rag_top_k: int = 5
    policy_rag_min_score: float = 0.72
    policy_rag_refuse_score: float = 0.45
    policy_rag_search_mode: str = "hybrid"
    policy_rag_hybrid_alpha: float = 0.5

    # Submission review: cap parallel LLM / extraction calls (receipts + policy agents)
    review_max_concurrency: int = 4

    storage_path: Path = Path("./storage")
    policies_path: Path = Path("./policies")
    submissions_path: Path = Path("./submissions")


settings = Settings()
settings.storage_path.mkdir(parents=True, exist_ok=True)
