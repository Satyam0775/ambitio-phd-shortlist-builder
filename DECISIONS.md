# DECISIONS.md

## PhD Shortlist Builder — Design Decisions

**Ambitio AI Engineer Take-Home Assignment**

This document covers the seven data-quality challenges addressed in the implementation, the concrete design decisions made for each, and the trade-offs accepted within the 72-hour constraint.

---

### Challenge 1: Same-Name-Different-Person Collisions

**Problem.** "Wei Wang", "Priya Sharma", "Rong Zheng" each map to dozens of distinct researchers in OpenAlex. A system that treats any name returned by a keyword search as a valid supervisor will routinely surface the wrong human — a biomedical informatics "Wei Wang" in Singapore when the student targets a clinical NLP "Wei Wang" in Boston.

**Design decision.** The system uses a two-layer identity verification approach rather than trusting any single database record.

*Layer 1 — OpenAlex disambiguation.* OpenAlex performs its own author disambiguation using co-author networks, affiliation history, and ORCID when available. By using OpenAlex author IDs (e.g. `A5023442007`) as the primary identifier throughout the pipeline, we inherit their disambiguation as a starting point.

*Layer 2 — Semantic Scholar cross-check.* Every candidate is re-verified against Semantic Scholar using a decomposed scoring formula: name match (0–0.40), institution token-overlap (0–0.35), and topic consistency from paper fieldsOfStudy and title keywords (0–0.25). A minimum combined score of **0.35** is required to mark an author as verified. The institution match uses Jaccard-style token overlap — "Massachusetts Institute of Technology" and "MIT" share the token `mit` and both resolve correctly — rather than requiring an exact string match. If an ORCID is present on the OpenAlex record, the system uses it as a fast-path to bypass name-based matching entirely, achieving a confidence of 0.95 automatically.

**Trade-off.** SS verification is probabilistic, not deterministic. A threshold of 0.35 was chosen to keep recall high enough to produce 50+ recommendations while suppressing obvious collisions. A higher threshold (0.50) would reduce contamination further but would discard legitimate matches where affiliation data is sparse.

**Limitation.** Researchers who have not registered an ORCID, whose SS affiliation strings differ substantially from OpenAlex institution names, or who publish under multiple name variants (with/without middle initial) are harder to match. The SS verification cap of 30 candidates means some candidates are forwarded to ranking without SS verification at all — they rely on the domain and faculty filters for contamination control.

---

### Challenge 2: Career-Stage Errors

**Problem.** PhD students and postdocs appear in academic databases with first-author papers. NIH F31/F32 fellowship awardees are listed as principal investigators on their own grants despite being early-career researchers. A system treating any author as a potential supervisor will surface 24-year-old doctoral students to applicants expecting tenured faculty.

**Design decision.** The `FacultyVerifier` (`app/verification/faculty_verifier.py`) applies a three-stage gate.

*Stage 1 — Hard pattern rejection.* Regular expressions match title strings against negative patterns: `phd student`, `doctoral candidate`, `postdoc`, `post-doctoral`, `research assistant`, `msca fellow`, `nih f31`, `nih f32`, `ukri studentship`. Any match returns `confidence = 0.0` immediately, with no further scoring.

*Stage 2 — Industry organisation rejection.* Researchers at Google, DeepMind, OpenAI, Meta, NVIDIA, Microsoft Research, Amazon, Apple, IBM Research, Adobe, and 10 other non-academic organisations are rejected unless their title string explicitly contains `professor`, `adjunct`, `principal investigator`, `faculty`, or `distinguished scientist`. When the title does qualify, confidence is capped at 0.65 — lower than pure-academia professors — because industry faculty appointments are often adjunct or visiting roles with limited PhD supervision authority.

*Stage 3 — Metric heuristics.* When no title data is available in OpenAlex (a common gap), the verifier uses h-index and citation count as proxies: `h ≥ 5` scores +0.35, `h ≥ 20` adds +0.20, `h ≥ 35` adds +0.15, `works ≥ 10` adds +0.15. A PhD student rarely exceeds h=6 with 40+ papers. The faculty eligibility threshold is set at **0.25**, with the ranking engine applying a secondary gate of **0.15** as a backstop for candidates that passed Step 8 under relaxation.

**Trade-off.** A prolific postdoc with h-index 12, no title data in OpenAlex, and 15 papers will pass the heuristic filter. This is an acknowledged gap. The domain verifier and ranking score typically suppress these researchers below the output threshold, but not always.

**Limitation.** OpenAlex has sparse title data for many researchers, especially outside the US and UK. The heuristic therefore does significant work in practice. A researcher with h=8 and 12 papers can pass with confidence 0.50, which may include junior researchers who have not yet established an independent lab.

---

### Challenge 3: Wrong-Domain Leakage from Keyword Overlap

**Problem.** A grant titled "biodegradable plastic cartridges" matches a biomaterials student. A "trauma-informed" grant matches a clinical psychology student but is actually a literary-history project on grief in Roman antiquity. Shared vocabulary does not imply shared discipline.

**Design decision.** The `DomainVerifier` (`app/verification/domain_verifier.py`) produces a 0–1 domain match score with four components applied in sequence.

*Component 1 — Concept overlap with precision weighting.* Student interest terms are matched bi-directionally against researcher OpenAlex concepts using substring and word-level matching. Terms from a curated `NLP_PRECISION_TERMS` set (35 highly specific terms: `named entity recognition`, `biomedical nlp`, `clinical text mining`, `biobert`, `de-identification`, etc.) receive 2× credit when they match exactly. This rewards researchers who explicitly work in the student's niche over researchers who share surface vocabulary.

*Component 2 — Keyword hit score.* Student terms are counted in the researcher's combined text (concepts + paper titles). This catches cases where the concept taxonomy is sparse but paper titles are informative.

*Component 3 — Title relevance bonus (supporting signal only).* Paper title matching was found to be easily gamed — a researcher publishing one paper with "clinical" in the title scored as highly as one whose entire body of work is in clinical NLP. The formula was changed from `×3` scaling to `×1.5` capped at 0.5, making title matching a supporting signal rather than a trump card.

*Component 4 — Discipline penalty multiplier.* A four-bucket discipline system (humanities, STEM, medical, social science) classifies both researcher and student. Cross-bucket penalties are applied: humanities vs STEM returns 0.05 (near-zero), effectively rejecting Roman antiquity researchers for NLP students. Medical vs STEM returns 0.85 (soft penalty) to avoid over-penalising biomedical NLP researchers who legitimately span both domains.

*Component 5 — NLP context penalty.* When the student's interests contain NLP signals (`nlp`, `natural language processing`, `ehr`, `bert`, etc.) and the researcher's text contains zero NLP-precision signals, a 0.40 multiplier is applied. This specifically blocks epidemiologists, hospital administrators, and general healthcare researchers who share `clinical` and `patient` vocabulary but publish no NLP work. In validation: an epidemiologist scores 0.00, a hospital administrator scores 0.007, against a clinical NLP student.

**Trade-off.** The NLP context penalty is hardcoded for NLP-focused students. Students in other domains (quantum computing, biomaterials) benefit from the discipline bucket system but not from a domain-specific precision layer.

**Limitation.** The discipline bucket classification uses keyword presence in free text, not structured ontology lookups. A researcher who works on "computational social science" may be classified as either STEM or social_science depending on which terms appear more frequently in their text, introducing inconsistency.

---

### Challenge 4: Country Adherence

**Problem.** The assignment specifies target countries as a hard constraint. A wrong-country recommendation has zero actionable value — an Indian student cannot apply to a German fellowship restricted to EU residents, regardless of research fit.

**Design decision.** Country filtering is applied at two points, both before domain verification and ranking.

*Step 5 — Hard filter.* After OpenAlex retrieval, every candidate's `country_code` (ISO-2) is checked against the target set. Candidates outside the target set are removed unconditionally. The filter runs in Python on the candidate list rather than relying solely on the OpenAlex API filter — the API filter was found to miss some candidates whose country code appears on the authorship record but not on the author record.

`country_map.py` normalises free-text country names to ISO-2 codes: "United Kingdom", "UK", "Great Britain", and "England" all resolve to `GB`. The mapping covers 60+ country name variants.

*Guard.* If the country filter removes too aggressively (< 100 candidates remain when 100+ were retrieved), researchers with an empty/unknown `country_code` are added back. This handles the case where OpenAlex has not yet resolved an author's current institution.

*Ranking.* The `country_match` component (10% weight) provides a final tiebreaker at the ranking stage, reinforcing the upstream hard filter.

**Trade-off.** Including unknown-country candidates as a guard risks surfacing researchers who are outside the target countries but whose country data is simply missing from OpenAlex. This was chosen over the alternative (returning fewer than 50 recommendations) because assignment requirement 1 (coverage ≥ 50) and requirement 2 (100% country adherence) are in tension when data is sparse.

**Limitation.** Country filtering at the API level for OpenAlex works-based retrieval was unreliable enough to require a Python-side re-filter. This means some non-target-country candidates are retrieved and filtered out, adding unnecessary API calls. A future improvement would be to query OpenAlex with tighter affiliation filters, accepting lower recall in exchange for a cleaner retrieval pool.

---

### Challenge 5: Ranking Quality — Prioritising Fit Over Fame

**Problem.** A naive ranking by citation count or h-index surfaces famous researchers who have no topical overlap with the student. A famous oncologist should not appear in a clinical NLP shortlist simply because they have h=60.

**Design decision.** The ranking formula uses five components weighted to prioritise research fit and career-stage validity over prestige metrics.

```
score = 0.40 × interest_match        (FAISS cosine similarity)
      + 0.25 × publication_relevance  (domain + h-index + paper relevance + citations)
      + 0.15 × faculty_confidence     (FacultyVerifier output)
      + 0.10 × recent_activity        (publication recency decay)
      + 0.10 × country_match          (hard-filtered upstream; tiebreaker)
```

`publication_relevance` itself weights domain match at 50%, ensuring topical alignment dominates over h-index (25%) and citation count (10%). Both h-index and citations are log-scaled — h=40 saturates to 1.0, preventing citation-count monopoly.

`recent_activity` decays: 1.0 for papers within the last year, 0.25 for 5–8 years ago, 0.10 for older. A researcher with last publication in 2018 is unlikely to be recruiting for 2025 intake regardless of their h-index. The ranking engine hard-excludes researchers whose last confirmed publication is > 5 years ago entirely, before any score is computed.

Institution diversity is enforced by capping at 5 recommendations per institution, ensuring the output spans multiple labs rather than concentrating in one large department.

Feedback multipliers from `outcomes.csv` are applied as a final multiplicative adjustment: `final_score = base_score × feedback_multiplier`, clamped to [0.0, 1.0].

**Trade-off.** The 40% weight on FAISS semantic similarity can over-rank researchers whose text is semantically close to the student's profile but whose actual publications do not address the student's specific question. This is partially corrected by the `publication_relevance` domain match component, but the tension between semantic similarity and precise topical alignment remains.

**Limitation.** Paper-level relevance scores (`_paper_relevance`) use keyword substring matching against the student's interest terms. A paper titled "Clinical Information Extraction Using Transformers" scores high for an NLP student, but the same researcher may primarily publish in unrelated areas — the system scores based on available paper titles, not on the proportion of their work that is relevant.

---

### Challenge 6: Feedback Learning — Closing the Loop

**Problem.** The shortlist is not a one-shot product. After students email supervisors, outcomes tell us which recommendations were genuinely useful (ADMIT, INTERVIEW) and which were contaminated (WRONG_PERSON, BOUNCE). Without feeding this signal back, the system cannot improve.

**Design decision.** `FeedbackProcessor` (`app/feedback_learning/feedback_processor.py`) ingests `outcomes.csv` and computes per-supervisor and per-institution score multipliers.

Outcome weights reflect assignment-specific priorities:

| Outcome | Weight | Rationale |
|---|---|---|
| ADMIT | +0.40 | Strongest signal — PI actively admitted a student |
| INTERVIEW | +0.25 | PI is recruiting and responsive |
| POSITIVE_REPLY | +0.12 | Engaged; surface again |
| WRONG_PERSON | −0.35 | Strongest penalty — system contamination |
| NOT_RECRUITING | −0.20 | Strong negative for current cycle |
| REJECT | −0.12 | PI reviewed but declined |
| BOUNCE | −0.08 | Email data unreliable |
| NO_REPLY | −0.03 | Weak signal; many legitimate causes |

Multipliers are clipped to [0.15, 2.5]. Outcomes decay with a 180-day half-life — an ADMIT from 6 months ago contributes at 50% weight. Institution-level signals are tracked at 25% of supervisor-level weight, providing a softer signal about which departments are generally receptive.

**Trade-off.** Feedback from one student is used to adjust rankings for all future students in the same area. This is appropriate when outcomes reflect genuine PI recruiting behaviour, but can cause over-fitting if a PI rejected one student due to research mismatch rather than a general "not recruiting" state.

**Limitation.** The current implementation applies multipliers uniformly across research areas. A PI who admitted a PTSD psychology student should not necessarily be boosted for a clinical NLP student in the same system. A future improvement would condition multipliers on the `area` column of `outcomes.csv`.

---

### Challenge 7: Semantic Scholar API Reliability

**Problem.** Semantic Scholar's free tier limits requests to 1 per second. With 100+ candidates queued for verification, the original implementation called `time.sleep(15)` on every HTTP 429 response, blocking the pipeline for minutes and repeating on each subsequent candidate. In observed runs, this added 10–20 minutes to the total runtime.

**Design decision.** A `_disabled` safe-mode flag replaces the sleep-loop pattern.

On the first HTTP 429, the client waits 3 seconds and retries once. If the retry also returns 429, `_disable("persistent HTTP 429")` is called, setting `self._disabled = True` and logging a single warning. All subsequent calls to `verify_author_identity()`, `search_author()`, and related methods return `_unverified()` or empty lists instantly — no sleep, no retry, no blocking. The pipeline continues with SS verification effectively disabled for the remainder of the run.

HTTP 429 is handled through a dedicated `_RateLimitError` exception class that bypasses the tenacity retry decorator (which would otherwise retry transient network errors up to 3 times). This ensures 429 errors exit the retry loop immediately rather than sleeping through all retry attempts.

The verification cap `MAX_TO_VERIFY = 30` reduces the probability of hitting the rate limit by reducing the total number of SS requests per run. Candidates are sorted by domain score before the cap is applied, so the 30 candidates verified are the topically strongest ones.

**Trade-off.** Disabling SS verification mid-run means some candidates do not receive the name/institution/topic cross-check. These candidates rely entirely on OpenAlex data quality and the domain/faculty filters for contamination control.

**Limitation.** A Semantic Scholar API key (free to request) raises the rate limit to 10 requests per second, making the safe-mode largely unnecessary. The implementation supports the key via `SEMANTIC_SCHOLAR_API_KEY` in `.env`, but the safe-mode behaviour is retained as a guard for keyless execution.

---

## Trade-offs Chosen Under the 72-Hour Constraint

**Precision over recall at every filter stage.** Each filtering step (domain: 0.15, faculty: 0.25, SS match: 0.35, recency: 5 years) was set to err toward exclusion rather than inclusion. The assignment states that surfacing a wrong-person is worse than missing a borderline-correct one. This means the system reliably produces clean shortlists of 50–80 recommendations rather than noisy shortlists of 150+.

**Deterministic `why_match` by default.** Generating Gemini explanations for 50–100 recommendations per run would exhaust the free API quota. The rule-based fallback produces factual, evidence-anchored explanations (referencing actual paper titles and shared topics) without any API call. The quality is lower than Gemini output but the pipeline never fails due to quota exhaustion.

**Works-based retrieval over concept-based retrieval.** OpenAlex's `x_concepts.id` filter on the `/authors` endpoint consistently returned zero results in testing despite valid concept IDs — a known API behaviour not covered in public documentation. The works-based approach (search works → extract author authorships) was substituted as the primary retrieval path. This is more reliable but extracts authors from all positions in the authorship list, requiring the faculty verifier to do more work distinguishing PIs from co-authors.

**30-candidate SS verification cap.** A cap of 100 would be more thorough but risks 60–90 seconds of blocked execution when rate limits hit. A cap of 30 covers the strongest domain matches (candidates are sorted by domain score before the cap) and keeps runtime within the 15-minute target.

**No PhD programme URL extraction.** Scraping individual department admission pages to extract eligibility restrictions ("UK only", "home fees") was identified as a high-value improvement but was out of scope for the constraint window. The `programs` field is populated from OpenAlex structured data where available and left empty otherwise.