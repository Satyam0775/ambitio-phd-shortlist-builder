# Schema Documentation

**PhD Shortlist Builder — Ambitio AI Engineer Assignment**

This document defines the complete input and output JSON schemas for the PhD Shortlist Builder pipeline.

---

## Table of Contents

- [Input Schema — Student Profile](#input-schema--student-profile)
  - [Top-Level Fields](#top-level-fields)
  - [education\[\]](#education)
  - [projects\[\]](#projects)
  - [publications\[\]](#publications)
- [Output Schema — Shortlist](#output-schema--shortlist)
  - [Top-Level Fields](#top-level-fields-1)
  - [recommendations\[\]](#recommendations)
  - [evidence.papers\[\]](#evidencepapers)
  - [evidence.grants\[\]](#evidencegrants)
  - [Tier Definitions](#tier-definitions)
  - [Score Formula](#score-formula)
- [Complete Input Example](#complete-input-example)
- [Complete Output Example](#complete-output-example)

---

## Input Schema — Student Profile

The pipeline accepts a single JSON object representing one student.

### Top-Level Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `student_id` | `string` | **Required** | Unique student identifier. Used as the filename base for the output JSON (`sample_output/<student_id>.json`). |
| `name` | `string` | **Required** | Full name of the student. Used in `why_match` generation and logging. |
| `education` | `array<EducationEntry>` | **Required** | Chronological list of degrees held. Must contain at least one entry. See [education\[\]](#education). |
| `skills` | `array<string>` | **Required** | Technical skills, tools, and frameworks. Used in embedding generation and keyword extraction. Example: `["PyTorch", "BERT", "clinical text mining"]`. |
| `projects` | `array<ProjectEntry>` | Optional | Research or engineering projects. Titles and descriptions are used in keyword generation. See [projects\[\]](#projects). |
| `publications` | `array<PublicationEntry>` | Optional | Student's own publications. Titles are used in domain scoring and keyword generation. See [publications\[\]](#publications). |
| `research_interests` | `array<string>` | **Required** | Stated research interest areas (3–5 recommended). These are the primary input to Gemini normalization and the basis for retrieval keyword generation. Example: `["biomedical NLP", "clinical information extraction"]`. |
| `target_countries` | `array<string>` | **Required** | Countries the student is willing to study in. Applied as a **hard filter** — no recommendation outside this list will appear in the output. Accepts full names, common abbreviations, and ISO-2 codes: `"United States"`, `"UK"`, `"CA"`. |
| `target_intake` | `string` | Optional | Target start semester and year. Free text. Example: `"Fall 2025"`. Not used in filtering; included for reference in downstream workflows. |
| `intro_call_summary` | `string` | Optional | Free-text summary from the student onboarding call. Provides additional context for Gemini interest normalization and profile summarization. Truncated to 300 characters during processing. |
| `raw_resume` | `string` | Optional | Full resume text. Used as supplementary context for keyword generation. Not parsed structurally. |

### education[]

Each entry in the `education` array is an object with the following fields.

| Field | Type | Required | Description |
|---|---|---|---|
| `degree` | `string` | **Required** | Degree name. Example: `"M.Tech in Computer Science"`, `"PhD in Biomedical Informatics"`. |
| `institution` | `string` | **Required** | Name of the awarding institution. |
| `year` | `integer` | Optional | Year of graduation. |
| `gpa` | `string` | Optional | Grade or GPA in any format. Example: `"9.2/10"`, `"3.9/4.0"`, `"First Class Honours"`. |
| `thesis` | `string` | Optional | Thesis or dissertation title. Strongly recommended — thesis titles are the highest-signal input for generating relevant retrieval keywords. |
| `field` | `string` | Optional | Broad field of study. Example: `"Natural Language Processing"`, `"Computer Engineering"`. |

### projects[]

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | `string` | **Required** | Project name. |
| `description` | `string` | Optional | Brief description of the project and its outcomes. |
| `technologies` | `array<string>` | Optional | Tools, frameworks, or methods used. |

### publications[]

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | `string` | **Required** | Full paper title. |
| `venue` | `string` | Optional | Conference or journal name. |
| `year` | `integer` | Optional | Publication year. |
| `url` | `string` | Optional | URL to the paper (DOI, arXiv, ACL Anthology, etc.). |

---

## Output Schema — Shortlist

The pipeline writes a single JSON object per student.

### Top-Level Fields

| Field | Type | Description |
|---|---|---|
| `student_id` | `string` | Echoed from the input profile. |
| `generated_at` | `string` (ISO 8601) | UTC timestamp of pipeline completion. Format: `"2025-01-15T10:23:44.123456"`. |
| `total_candidates_evaluated` | `integer` | Number of raw researcher candidates retrieved from OpenAlex before filtering. Useful for auditing retrieval coverage. |
| `recommendations` | `array<Recommendation>` | Ranked list of supervisor recommendations, sorted by `score` descending. Length: 50–200. See [recommendations\[\]](#recommendations). |

### recommendations[]

Each entry in the `recommendations` array is a `Recommendation` object.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `supervisor_id` | `string` | No | OpenAlex author identifier. Globally unique and stable. Format: `"A5023442007"`. Can be resolved at `https://openalex.org/authors/<id>`. |
| `name` | `string` | No | Full display name of the supervisor as recorded in OpenAlex. |
| `institution` | `string` | No | Current primary institutional affiliation. Sourced from OpenAlex `last_known_institution`. |
| `country` | `string` | No | Country name of the institution. Canonical form (e.g. `"United States"`, `"United Kingdom"`). Always within the student's `target_countries`. |
| `email` | `string` | Yes | Direct email address if retrievable from OpenAlex or institution homepage. `null` in the majority of cases — contact details should be looked up via the homepage URL. |
| `research_focus` | `array<string>` | No | Top research topics derived from OpenAlex concept tags and paper-level topics. Maximum 8 entries. Ordered by relevance. |
| `evidence` | `EvidenceBlock` | No | Verifiable publications and grants supporting the recommendation. See [evidence.papers\[\]](#evidencepapers) and [evidence.grants\[\]](#evidencegrants). Always contains at least one paper — recommendations with no retrievable evidence are excluded from output. |
| `why_match` | `string` | No | Personalised explanation of why this supervisor is a strong match for this specific student. References actual paper titles and shared topics. Generated deterministically by default; optionally generated by Gemini when `ENABLE_GEMINI_WHY_MATCH=True`. Never empty. |
| `tier` | `string` (enum) | No | Tier classification based on composite score. One of `"reach"`, `"target"`, `"safety"`. See [Tier Definitions](#tier-definitions). |
| `score` | `float` [0.0, 1.0] | No | Composite ranking score. Rounded to 4 decimal places. See [Score Formula](#score-formula). |
| `programs` | `array<string>` | No | Linked PhD programme names or URLs associated with this supervisor. Populated from structured OpenAlex institution data. Empty array when not available. |
| `faculty_confidence` | `float` [0.0, 1.0] | No | Probability that the researcher is a supervising-eligible faculty member, as determined by `FacultyVerifier`. Based on title pattern matching (professor, PI, reader, etc.) and h-index heuristics. Rounded to 3 decimal places. |
| `h_index` | `integer` | No | H-index of the researcher. Maximum of OpenAlex and Semantic Scholar values when both are available. |
| `total_citations` | `integer` | No | Total citation count. Maximum of OpenAlex and Semantic Scholar values. |
| `most_recent_paper_year` | `integer` | Yes | Year of the most recent paper found in OpenAlex for this researcher. `null` if no papers were retrieved. Used in the `recent_activity` scoring component and the 5-year inactivity filter. |
| `homepage_url` | `string` | Yes | URL of the researcher's lab or personal academic homepage. Sourced from OpenAlex. `null` if not available. |
| `orcid` | `string` | Yes | ORCID identifier in URI form. Format: `"https://orcid.org/0000-0000-0000-0000"`. `null` if not registered or not available in OpenAlex. |

### evidence.papers[]

Each entry in `evidence.papers` is a `PaperRecord` object.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `title` | `string` | No | Full paper title. |
| `year` | `integer` | Yes | Publication year. `null` if not recorded in OpenAlex. |
| `url` | `string` | Yes | Landing page URL — DOI redirect, OpenAlex record, or publisher page. Format: `"https://openalex.org/W2125481095"` or direct DOI. |
| `venue` | `string` | Yes | Journal or conference name. `null` if not available. |
| `citation_count` | `integer` | No | Total citations received as recorded in OpenAlex. |

Up to 5 papers are included per recommendation, selected by highest `citation_count`.

### evidence.grants[]

Each entry in `evidence.grants` is a `GrantRecord` object. This field is sparsely populated — grant data from OpenAlex is available primarily for US and EU researchers.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `title` | `string` | No | Grant or project title. |
| `funder` | `string` | Yes | Funding organisation. Example: `"NIH"`, `"NSF"`, `"Wellcome Trust"`. |
| `year` | `integer` | Yes | Award year. |
| `url` | `string` | Yes | Link to the grant record. |
| `amount` | `string` | Yes | Funding amount as a formatted string. `null` in most cases — OpenAlex does not consistently provide amounts. |

### Tier Definitions

| Tier | Score Range | Interpretation |
|---|---|---|
| `reach` | ≥ 0.75 | Strong domain match, senior faculty, highly cited, active recent publications. Worth contacting even if the student profile is not an exact fit for the lab's current intake. |
| `target` | 0.50 – 0.74 | Good topical alignment, verified faculty, solid publication record. These are the primary emailing targets. |
| `safety` | 0.30 – 0.49 | Plausible match with a lower confidence score. May reflect less prominent labs, older publications, or weaker domain alignment. Worth including for coverage. |

Recommendations with `score < 0.30` are excluded from output entirely.

### Score Formula

```
score = 0.40 × interest_match
      + 0.25 × publication_relevance
      + 0.15 × faculty_confidence
      + 0.10 × recent_activity
      + 0.10 × country_match
```

| Component | Source | Notes |
|---|---|---|
| `interest_match` | FAISS cosine similarity (all-MiniLM-L6-v2) | Student profile embedding vs researcher profile embedding. Range: 0–1. |
| `publication_relevance` | Composite: 50% domain match + 25% h-index + 15% paper relevance + 10% citations | H-index log-scaled (saturates at h=40). Citations log-scaled (saturates at 10 000). |
| `faculty_confidence` | FacultyVerifier output | See `faculty_confidence` field above. |
| `recent_activity` | Publication recency decay | 1.0 if last paper ≤ 1 year ago; 0.85 at 2 years; 0.50 at 5 years; 0.10 beyond 8 years. Researchers with no confirmed publication in the last 5 years are excluded before scoring. |
| `country_match` | Hard-filtered upstream | 1.0 if researcher country is in `target_countries`; 0.0 otherwise. Functions as a tiebreaker since country filtering is enforced at Step 5. |

If a feedback outcomes file is loaded, a per-supervisor multiplier (range: 0.15–2.50) is applied to the composite score and the result is clamped to [0.0, 1.0].

---

## Complete Input Example

```json
{
  "student_id": "AMB-2024-1142",
  "name": "Priya Sharma",
  "education": [
    {
      "degree": "M.Tech in Computer Science",
      "institution": "Indian Institute of Technology Bombay",
      "year": 2023,
      "gpa": "9.2/10",
      "thesis": "Transformer-based models for clinical named entity recognition in low-resource settings",
      "field": "Natural Language Processing"
    },
    {
      "degree": "B.Tech in Computer Engineering",
      "institution": "BITS Pilani",
      "year": 2021,
      "gpa": "8.8/10",
      "field": "Computer Engineering"
    }
  ],
  "skills": [
    "Python", "PyTorch", "TensorFlow", "Hugging Face Transformers",
    "spaCy", "BERT", "GPT fine-tuning", "scikit-learn",
    "SQL", "MongoDB", "Docker", "Git", "Linux",
    "biomedical NLP", "clinical text mining"
  ],
  "projects": [
    {
      "title": "MedNER: Clinical NER on MIMIC-III",
      "description": "Fine-tuned BioBERT for named entity recognition on clinical notes, achieving 89.3 F1 on i2b2 benchmark",
      "technologies": ["PyTorch", "Hugging Face", "BioBERT"]
    },
    {
      "title": "Low-resource cross-lingual transfer for biomedical texts",
      "description": "Investigated zero-shot cross-lingual transfer for Hindi medical texts using multilingual BERT",
      "technologies": ["mBERT", "Python", "fastText"]
    }
  ],
  "publications": [
    {
      "title": "Improving Clinical NER in Low-Resource Settings via Cross-Lingual Transfer",
      "venue": "ACL BioNLP Workshop",
      "year": 2023,
      "url": "https://example.com/paper1"
    }
  ],
  "research_interests": [
    "biomedical natural language processing",
    "clinical information extraction",
    "large language models for healthcare",
    "low-resource NLP",
    "electronic health record mining"
  ],
  "target_countries": ["United States", "Canada", "United Kingdom", "Australia"],
  "target_intake": "Fall 2025",
  "intro_call_summary": "Priya is a strong NLP engineer with clear focus on biomedical/clinical applications. She wants a research-focused PhD, ideally in a lab that does both foundational NLP research and clinical application. She's particularly interested in LLMs for clinical decision support, de-identification, and patient phenotyping. She is open to CS or Biomedical Informatics departments.",
  "raw_resume": "M.Tech thesis on transformer-based clinical NER. Published at ACL BioNLP 2023. Interned at Tata Consultancy Services AI lab. GATE score 98 percentile. Strong publication record for a Masters student. Looking for funded PhD positions starting Fall 2025."
}
```

---

## Complete Output Example

```json
{
  "student_id": "AMB-2024-1142",
  "generated_at": "2025-01-15T10:23:44.123456",
  "total_candidates_evaluated": 387,
  "recommendations": [
    {
      "supervisor_id": "A5023442007",
      "name": "Ozlem Uzuner",
      "institution": "George Mason University",
      "country": "United States",
      "email": null,
      "research_focus": [
        "clinical natural language processing",
        "electronic health records",
        "named entity recognition",
        "information extraction",
        "biomedical text mining"
      ],
      "evidence": {
        "papers": [
          {
            "title": "2010 i2b2/VA challenge on concepts, assertions, and relations in clinical text",
            "year": 2011,
            "url": "https://openalex.org/W2125481095",
            "venue": "Journal of the American Medical Informatics Association",
            "citation_count": 1203
          },
          {
            "title": "Extracting medication information from clinical text",
            "year": 2010,
            "url": "https://openalex.org/W2032456789",
            "venue": "JAMIA",
            "citation_count": 467
          }
        ],
        "grants": []
      },
      "why_match": "Prof. Uzuner's foundational work on clinical NER benchmarks, including the i2b2 challenge series, maps directly onto Priya's M.Tech thesis on transformer-based clinical NER in low-resource settings. Her lab's continued focus on EHR information extraction and clinical text de-identification aligns precisely with Priya's stated interest in clinical decision support and patient phenotyping.",
      "tier": "reach",
      "score": 0.8821,
      "programs": [],
      "faculty_confidence": 0.95,
      "h_index": 41,
      "total_citations": 12400,
      "most_recent_paper_year": 2024,
      "homepage_url": "https://masonchc.gmu.edu/",
      "orcid": null
    },
    {
      "supervisor_id": "A5017654321",
      "name": "Dina Demner-Fushman",
      "institution": "National Library of Medicine, NIH",
      "country": "United States",
      "email": null,
      "research_focus": [
        "biomedical NLP",
        "clinical question answering",
        "medical information retrieval",
        "EHR processing"
      ],
      "evidence": {
        "papers": [
          {
            "title": "Towards clinical question answering",
            "year": 2022,
            "url": "https://openalex.org/W2234567890",
            "venue": "npj Digital Medicine",
            "citation_count": 312
          }
        ],
        "grants": []
      },
      "why_match": "Prof. Demner-Fushman leads the Biomedical Language Processing group at NLM/NIH, directly working on LLMs for clinical information extraction and question answering. Her recent work on clinical question answering directly overlaps with Priya's EHR mining interests.",
      "tier": "reach",
      "score": 0.854,
      "programs": [],
      "faculty_confidence": 0.90,
      "h_index": 38,
      "total_citations": 9800,
      "most_recent_paper_year": 2024,
      "homepage_url": null,
      "orcid": null
    },
    {
      "supervisor_id": "A5011223344",
      "name": "Timothy Miller",
      "institution": "Boston Children's Hospital / Harvard Medical School",
      "country": "United States",
      "email": null,
      "research_focus": [
        "clinical NLP",
        "biomedical text mining",
        "low-resource NLP",
        "neural models for clinical text"
      ],
      "evidence": {
        "papers": [
          {
            "title": "Extracting adverse drug events from clinical notes",
            "year": 2021,
            "url": "https://openalex.org/W2345678901",
            "venue": "JAMIA",
            "citation_count": 189
          }
        ],
        "grants": []
      },
      "why_match": "Prof. Miller's work at Boston Children's Hospital focuses on NLP for pediatric clinical notes and adverse event extraction, connecting directly to Priya's interest in clinical NER and EHR mining. His lab has worked on low-resource scenarios for clinical text, directly matching Priya's thesis focus on low-resource transfer.",
      "tier": "target",
      "score": 0.7623,
      "programs": [],
      "faculty_confidence": 0.85,
      "h_index": 22,
      "total_citations": 3400,
      "most_recent_paper_year": 2024,
      "homepage_url": null,
      "orcid": null
    }
  ]
}
```