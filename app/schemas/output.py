from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EvidenceBlock(BaseModel):
    papers: list[dict] = Field(default_factory=list)
    grants: list[dict] = Field(default_factory=list)


class SupervisorRecommendation(BaseModel):
    supervisor_id: str
    name: str
    institution: str
    country: str
    email: Optional[str] = None
    research_focus: list[str] = Field(default_factory=list)
    evidence: EvidenceBlock = Field(default_factory=EvidenceBlock)
    why_match: str = ""
    tier: str = "target"  # reach / target / safety
    score: float = 0.0
    programs: list[str] = Field(default_factory=list)
    faculty_confidence: float = 0.0
    h_index: int = 0
    total_citations: int = 0
    most_recent_paper_year: Optional[int] = None
    homepage_url: Optional[str] = None
    orcid: Optional[str] = None


class ShortlistOutput(BaseModel):
    student_id: str
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    total_candidates_evaluated: int = 0
    recommendations: list[SupervisorRecommendation] = Field(default_factory=list)
