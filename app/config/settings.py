import os
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    gemini_api_key: str = Field(default="", env="GEMINI_API_KEY")
    semantic_scholar_api_key: str = Field(default="", env="SEMANTIC_SCHOLAR_API_KEY")
    openalex_email: str = Field(default="contact@ambitio.club", env="OPENALEX_EMAIL")

    # Pipeline tuning — reduced defaults for faster, safer runs
    max_candidates: int = Field(default=100, env="MAX_CANDIDATES")
    final_shortlist_size: int = Field(default=50, env="FINAL_SHORTLIST_SIZE")
    request_timeout: int = Field(default=30, env="REQUEST_TIMEOUT")

    # Gemini feature flags
    # ENABLE_GEMINI_WHY_MATCH=False by default — why_match uses deterministic fallback.
    # Set to True in .env only when you have sufficient Gemini quota.
    enable_gemini_why_match: bool = Field(default=False, env="ENABLE_GEMINI_WHY_MATCH")

    # Ranking weights (used by ranking_engine.py via settings reference)
    weight_interest_match: float = 0.40
    weight_publication_relevance: float = 0.25
    weight_faculty_confidence: float = 0.15
    weight_country_match: float = 0.10
    weight_recent_activity: float = 0.10

    # Tier thresholds
    reach_threshold: float = 0.75
    target_lower: float = 0.50
    safety_threshold: float = 0.30

    # Faculty title keywords (used by faculty_verifier if needed)
    faculty_positive_titles: list[str] = [
        "professor",
        "associate professor",
        "assistant professor",
        "principal investigator",
        "pi ",
        "faculty",
        "lecturer",
        "reader",
        "chair",
        "director",
        "head of",
        "senior research scientist",
        "research professor",
    ]
    faculty_negative_titles: list[str] = [
        "phd student",
        "phd candidate",
        "doctoral student",
        "doctoral candidate",
        "graduate student",
        "postdoc",
        "postdoctoral",
        "post-doctoral",
        "research assistant",
        "research associate",
        "visiting student",
        "intern",
        "fellow",
    ]

    # API base URLs
    openalex_base_url: str = "https://api.openalex.org"
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()