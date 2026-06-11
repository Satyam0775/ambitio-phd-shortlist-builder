from pydantic import BaseModel, Field
from typing import Optional


class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: Optional[int] = None
    gpa: Optional[str] = None
    thesis: Optional[str] = None
    field: Optional[str] = None


class PublicationEntry(BaseModel):
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None


class ProjectEntry(BaseModel):
    title: str
    description: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)


class StudentProfile(BaseModel):
    student_id: str
    name: str
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    publications: list[PublicationEntry] = Field(default_factory=list)
    research_interests: list[str] = Field(default_factory=list)
    target_countries: list[str] = Field(default_factory=list)
    target_intake: Optional[str] = None
    intro_call_summary: Optional[str] = None
    raw_resume: Optional[str] = None
