from pydantic import BaseModel, Field
from typing import Optional


class PaperEvidence(BaseModel):
    title: str
    year: Optional[int] = None
    url: Optional[str] = None
    venue: Optional[str] = None
    citation_count: int = 0
    relevance_score: float = 0.0


class GrantEvidence(BaseModel):
    title: str
    funder: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    amount: Optional[str] = None


class ResearcherCandidate(BaseModel):
    """Raw candidate from OpenAlex before verification."""
    openalex_id: str
    name: str
    institution: Optional[str] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    email: Optional[str] = None
    concepts: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    recent_papers: list[PaperEvidence] = Field(default_factory=list)
    total_works: int = 0
    cited_by_count: int = 0
    h_index: int = 0
    last_known_institution: Optional[str] = None
    raw_title: Optional[str] = None  # job title from OpenAlex if available
    homepage_url: Optional[str] = None
    orcid: Optional[str] = None


class VerifiedResearcher(BaseModel):
    """Researcher that has passed all verification steps."""
    openalex_id: str
    semantic_scholar_id: Optional[str] = None
    name: str
    institution: str
    country_code: str
    country_name: str
    email: Optional[str] = None
    research_focus: list[str] = Field(default_factory=list)
    papers: list[PaperEvidence] = Field(default_factory=list)
    grants: list[GrantEvidence] = Field(default_factory=list)
    faculty_confidence: float = 0.0
    faculty_title: Optional[str] = None
    domain_match_score: float = 0.0
    semantic_scholar_verified: bool = False
    total_citations: int = 0
    h_index: int = 0
    most_recent_paper_year: Optional[int] = None
    orcid: Optional[str] = None
    homepage_url: Optional[str] = None
    programs: list[str] = Field(default_factory=list)
    # Embedding vector (not serialized to output)
    embedding: Optional[list[float]] = Field(default=None, exclude=True)
