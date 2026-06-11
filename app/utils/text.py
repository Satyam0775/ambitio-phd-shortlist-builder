import re
import string
from typing import Optional


def clean_text(text: Optional[str]) -> str:
    """Strip HTML tags, extra whitespace, and normalize."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Remove special chars but keep sentence structure
    text = re.sub(r"[^\w\s.,;:!?'\"-]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_keywords_from_text(text: str) -> list[str]:
    """Basic keyword extraction — stopword removal."""
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "up", "about", "into", "through", "during",
        "is", "was", "are", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may", "might",
        "shall", "can", "this", "that", "these", "those", "i", "you", "he", "she",
        "it", "we", "they", "what", "which", "who", "whom", "whose", "when",
        "where", "why", "how", "all", "both", "each", "few", "more", "most",
        "other", "some", "such", "no", "nor", "not", "only", "own", "same",
        "so", "than", "too", "very", "just", "also", "between", "within",
        "research", "study", "studies", "using", "based", "used",
    }
    words = re.findall(r"\b[a-z][a-z\-]{2,}\b", text.lower())
    return [w for w in words if w not in stop_words]


def normalize_name(name: str) -> str:
    """Normalize researcher name for collision detection."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", "", name)
    parts = name.split()
    # Sort parts to catch "Smith John" vs "John Smith"
    return " ".join(sorted(parts))


def build_researcher_text_blob(
    name: str,
    institution: str,
    topics: list[str],
    paper_titles: list[str],
) -> str:
    """Create a single text blob for embedding generation."""
    parts = [
        f"Researcher: {name}",
        f"Institution: {institution}",
        "Topics: " + ", ".join(topics),
        "Papers: " + "; ".join(paper_titles[:10]),
    ]
    return " ".join(parts)


def build_student_text_blob(
    name: str,
    interests: list[str],
    thesis_titles: list[str],
    skills: list[str],
) -> str:
    parts = [
        f"Student: {name}",
        "Research interests: " + ", ".join(interests),
        "Thesis/Projects: " + "; ".join(thesis_titles),
        "Skills: " + ", ".join(skills[:20]),
    ]
    return " ".join(parts)
