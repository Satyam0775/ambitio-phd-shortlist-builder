"""
OpenAlex API client.
Polite pool: sends mailto= param so OpenAlex gives us the polite tier.

PRIMARY RETRIEVAL STRATEGY:
  Works search → extract authors from authorships → aggregate → deduplicate.
  This is reliable and produces 100+ candidates consistently.

DEPRECATED (unreliable):
  x_concepts.id-based author search returns 0 results in practice.
  Kept only as optional enrichment; never blocks retrieval.
"""

import math
import time
import logging
from typing import Optional
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config.settings import settings
from app.schemas.researcher import ResearcherCandidate, PaperEvidence
from app.utils.logging import get_logger

logger = get_logger(__name__)

OPENALEX_BASE = settings.openalex_base_url
POLITE_PARAMS: dict = {"mailto": settings.openalex_email}

MIN_RAW_CANDIDATES = 100


def _build_country_filter_authors(country_codes: set[str]) -> str:
    if not country_codes:
        return ""
    codes = "|".join(sorted(country_codes))
    return f"last_known_institutions.country_code:{codes}"


def _build_country_filter_works(country_codes: set[str]) -> str:
    if not country_codes:
        return ""
    codes = "|".join(sorted(country_codes))
    return f"authorships.institutions.country_code:{codes}"


class OpenAlexClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _get(self, url: str, params: dict) -> dict:
        params = {**params, **POLITE_PARAMS}
        logger.debug("OpenAlex request: %s params=%s", url, params)
        resp = self.session.get(url, params=params, timeout=settings.request_timeout)
        if resp.status_code == 429:
            logger.warning("OpenAlex rate limit hit — sleeping 10s")
            time.sleep(10)
            resp = self.session.get(url, params=params, timeout=settings.request_timeout)
        resp.raise_for_status()
        return resp.json()

    # ==================================================================
    # PRIMARY RETRIEVAL: Works-based author discovery
    # ==================================================================

    def retrieve_authors_via_works(
        self,
        keywords: list[str],
        country_codes: set[str],
        per_page: int = 50,
        max_pages: int = 4,
        min_candidates: int = MIN_RAW_CANDIDATES,
    ) -> list[dict]:
        """
        PRIMARY retrieval method.
        1. Search works by each keyword.
        2. Extract ALL authors from authorships (not just first/last —
           senior PIs frequently appear as middle or last authors).
        3. Aggregate concepts and citation metadata across works per author.
        4. Deduplicate by OpenAlex author ID.
        5. Apply Python-side country filter after aggregation.
        6. Expand across keywords until min_candidates reached or exhausted.
        """
        seen_ids: set[str] = set()
        author_metadata: dict[str, dict] = {}
        total_works_found = 0

        for kw_idx, keyword in enumerate(keywords):
            logger.info(
                "[retrieve_authors_via_works] Keyword %d/%d: '%s' (candidates so far: %d)",
                kw_idx + 1, len(keywords), keyword, len(author_metadata),
            )

            # Try with country filter first
            works = self.search_works_by_keyword(
                keyword, country_codes, per_page=per_page, max_pages=max_pages,
                use_country_filter=bool(country_codes),
            )

            # Retry without country filter if empty
            if not works and country_codes:
                logger.info("  Works=0 with country filter. Retrying without...")
                works = self.search_works_by_keyword(
                    keyword, country_codes, per_page=per_page, max_pages=max_pages,
                    use_country_filter=False,
                )

            if not works:
                logger.info("  No works found for keyword '%s'. Skipping.", keyword)
                continue

            total_works_found += len(works)
            logger.info("  Works found: %d (cumulative: %d)", len(works), total_works_found)

            new_count = self._extract_and_aggregate_authors(works, author_metadata, seen_ids)
            logger.info(
                "  New authors this keyword: %d | Unique total (pre-country): %d",
                new_count, len(author_metadata),
            )

            if len(author_metadata) >= min_candidates * 3:
                # Generous buffer before country filter — stop early
                logger.info(
                    "  Buffer target %d reached. Stopping keyword expansion.",
                    min_candidates * 3,
                )
                break

        # Apply Python-side country filter AFTER aggregation
        if country_codes:
            pre = len(author_metadata)
            author_metadata = {
                aid: a for aid, a in author_metadata.items()
                if (a.get("last_known_institution") or {}).get("country_code", "") in country_codes
            }
            logger.info(
                "  Python country filter: %d → %d authors (kept %.0f%%)",
                pre, len(author_metadata),
                100 * len(author_metadata) / max(pre, 1),
            )

        results = list(author_metadata.values())

        # Diagnostics
        country_counts: dict[str, int] = {}
        for a in results:
            cc = (a.get("last_known_institution") or {}).get("country_code", "??")
            country_counts[cc] = country_counts.get(cc, 0) + 1
        top_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        logger.info(
            "[retrieve_authors_via_works] COMPLETE: works=%d unique_authors=%d top_countries=%s",
            total_works_found, len(results),
            ", ".join(f"{cc}:{n}" for cc, n in top_countries),
        )
        return results

    def _extract_and_aggregate_authors(
        self,
        works: list[dict],
        author_metadata: dict[str, dict],
        seen_ids: set[str],
    ) -> int:
        """
        Extract ALL authors (all positions) from works and aggregate metadata.
        Including middle/last authors captures senior PIs who rarely appear first.
        Returns count of NEW authors added.
        """
        new_count = 0

        for work in works:
            work_concepts = work.get("concepts", [])
            work_title = work.get("title", "") or ""
            work_year = work.get("publication_year")
            work_citations = work.get("cited_by_count", 0)

            for authorship in work.get("authorships", []):
                author = authorship.get("author") or {}
                author_id = author.get("id", "")
                if not author_id:
                    continue

                institutions = authorship.get("institutions") or []
                inst = institutions[0] if institutions else {}

                if author_id not in seen_ids:
                    seen_ids.add(author_id)
                    new_count += 1
                    author_metadata[author_id] = {
                        "id": author_id,
                        "display_name": author.get("display_name", ""),
                        "last_known_institution": inst,
                        "last_known_institutions": [inst] if inst else [],
                        "x_concepts": list(work_concepts),
                        "works_count": 1,
                        "cited_by_count": work_citations,
                        "summary_stats": {},
                        "orcid": author.get("orcid"),
                        "_work_titles": [work_title] if work_title else [],
                        "_work_years": [work_year] if work_year else [],
                        "_author_position": authorship.get("author_position", ""),
                    }
                else:
                    existing = author_metadata[author_id]

                    # Aggregate concepts
                    existing_concept_ids = {
                        c.get("id") for c in existing["x_concepts"] if c.get("id")
                    }
                    for c in work_concepts:
                        if c.get("id") and c["id"] not in existing_concept_ids:
                            existing["x_concepts"].append(c)
                            existing_concept_ids.add(c["id"])

                    existing["works_count"] = existing.get("works_count", 0) + 1
                    existing["cited_by_count"] = (
                        existing.get("cited_by_count", 0) + work_citations
                    )
                    if work_title:
                        existing["_work_titles"].append(work_title)
                    if work_year:
                        existing["_work_years"].append(work_year)

                    if not existing.get("last_known_institution") and inst:
                        existing["last_known_institution"] = inst
                        existing["last_known_institutions"] = [inst]

                    # Upgrade position signal: prefer first/last
                    cur = existing.get("_author_position", "")
                    new_pos = authorship.get("author_position", "")
                    if new_pos in ("first", "last") and cur not in ("first", "last"):
                        existing["_author_position"] = new_pos

        return new_count

    # ==================================================================
    # SECONDARY: Direct author search
    # ==================================================================

    def search_authors_by_keyword(
        self,
        keyword: str,
        country_codes: set[str],
        per_page: int = 50,
        max_pages: int = 4,
        use_country_filter: bool = True,
    ) -> list[dict]:
        results: list[dict] = []
        filter_str = ""
        if use_country_filter and country_codes:
            filter_str = _build_country_filter_authors(country_codes)

        logger.info(
            "OpenAlex keyword author search: keyword='%s' filter='%s'",
            keyword, filter_str,
        )

        for page in range(1, max_pages + 1):
            params = {
                "search": keyword,
                "sort": "cited_by_count:desc",
                "per_page": per_page,
                "page": page,
                "select": (
                    "id,display_name,last_known_institutions,x_concepts,"
                    "works_count,cited_by_count,summary_stats,orcid"
                ),
            }
            if filter_str:
                params["filter"] = filter_str
            try:
                data = self._get(f"{OPENALEX_BASE}/authors", params)
                items = data.get("results", [])
                if not items:
                    break
                results.extend(items)
                logger.info(
                    "  Author keyword page %d keyword='%s' → %d (total: %d)",
                    page, keyword, len(items), len(results),
                )
                if len(items) < per_page:
                    break
            except Exception as exc:
                logger.error("Author keyword search failed for '%s': %s", keyword, exc)
                break
        logger.info("Author keyword search '%s' total: %d", keyword, len(results))
        return results

    # ==================================================================
    # DEPRECATED: Concept-based author search
    # ==================================================================

    def search_authors_by_concept(
        self,
        concept_id: str,
        country_codes: set[str],
        per_page: int = 50,
        max_pages: int = 5,
        use_country_filter: bool = True,
    ) -> list[dict]:
        """DEPRECATED — x_concepts.id returns 0 in practice. Kept for enrichment only."""
        results: list[dict] = []
        filter_parts = [f"x_concepts.id:{concept_id}"]
        if use_country_filter and country_codes:
            cf = _build_country_filter_authors(country_codes)
            if cf:
                filter_parts.append(cf)
        filter_str = ",".join(filter_parts)

        for page in range(1, max_pages + 1):
            params = {
                "filter": filter_str,
                "sort": "cited_by_count:desc",
                "per_page": per_page,
                "page": page,
                "select": (
                    "id,display_name,last_known_institutions,x_concepts,"
                    "works_count,cited_by_count,summary_stats,orcid"
                ),
            }
            try:
                data = self._get(f"{OPENALEX_BASE}/authors", params)
                items = data.get("results", [])
                if not items:
                    break
                results.extend(items)
                if len(items) < per_page:
                    break
            except Exception as exc:
                logger.error("Concept author search failed: %s", exc)
                break

        if not results:
            logger.warning("Concept author search returned 0 for %s (expected).", concept_id)
        return results

    def search_authors_by_topic(
        self,
        topic_id: str,
        country_codes: set[str],
        per_page: int = 50,
        max_pages: int = 3,
        use_country_filter: bool = True,
    ) -> list[dict]:
        """Search authors by topic ID (newer /topics API)."""
        results: list[dict] = []
        filter_parts = [f"topics.id:{topic_id}"]
        if use_country_filter and country_codes:
            cf = _build_country_filter_authors(country_codes)
            if cf:
                filter_parts.append(cf)
        filter_str = ",".join(filter_parts)

        logger.info("Topic author search: topic_id=%s filter=%s", topic_id, filter_str)

        for page in range(1, max_pages + 1):
            params = {
                "filter": filter_str,
                "sort": "cited_by_count:desc",
                "per_page": per_page,
                "page": page,
                "select": (
                    "id,display_name,last_known_institutions,x_concepts,"
                    "works_count,cited_by_count,summary_stats,orcid"
                ),
            }
            try:
                data = self._get(f"{OPENALEX_BASE}/authors", params)
                items = data.get("results", [])
                if not items:
                    break
                results.extend(items)
                logger.info("  Topic author page %d → %d (total: %d)", page, len(items), len(results))
                if len(items) < per_page:
                    break
            except Exception as exc:
                logger.error("Topic author search failed: %s", exc)
                break
        return results

    # ==================================================================
    # Works search
    # ==================================================================

    def get_author_works(
        self,
        author_id: str,
        per_page: int = 25,
        max_pages: int = 2,
    ) -> list[dict]:
        works: list[dict] = []
        for page in range(1, max_pages + 1):
            params = {
                "filter": f"author.id:{author_id}",
                "sort": "publication_year:desc",
                "per_page": per_page,
                "page": page,
                "select": (
                    "id,title,publication_year,primary_location,concepts,"
                    "cited_by_count,topics,open_access"
                ),
            }
            try:
                data = self._get(f"{OPENALEX_BASE}/works", params)
                items = data.get("results", [])
                if not items:
                    break
                works.extend(items)
                if len(items) < per_page:
                    break
            except Exception as exc:
                logger.error("get_author_works failed for %s: %s", author_id, exc)
                break
        return works

    def get_author_details(self, author_id: str) -> Optional[dict]:
        try:
            data = self._get(
                f"{OPENALEX_BASE}/authors/{author_id}",
                {
                    "select": (
                        "id,display_name,last_known_institutions,affiliations,"
                        "orcid,x_concepts,works_count,cited_by_count,summary_stats"
                    )
                },
            )
            return data
        except Exception as exc:
            logger.error("get_author_details failed for %s: %s", author_id, exc)
            return None

    def search_works_by_keyword(
        self,
        keyword: str,
        country_codes: set[str],
        per_page: int = 50,
        max_pages: int = 3,
        use_country_filter: bool = True,
    ) -> list[dict]:
        works: list[dict] = []
        filter_parts = ["publication_year:>2018"]
        if use_country_filter and country_codes:
            cf = _build_country_filter_works(country_codes)
            if cf:
                filter_parts.append(cf)
        filter_str = ",".join(filter_parts)

        logger.info(
            "Works search: keyword='%s' filter='%s' per_page=%d max_pages=%d",
            keyword, filter_str, per_page, max_pages,
        )

        for page in range(1, max_pages + 1):
            params = {
                "search": keyword,
                "filter": filter_str,
                "sort": "cited_by_count:desc",
                "per_page": per_page,
                "page": page,
                "select": "id,title,publication_year,authorships,concepts,cited_by_count,topics",
            }
            try:
                data = self._get(f"{OPENALEX_BASE}/works", params)
                items = data.get("results", [])
                if not items:
                    logger.info("  Works page %d: 0 results (end)", page)
                    break
                works.extend(items)
                logger.info(
                    "  Works page %d/%d '%s' → %d (total: %d)",
                    page, max_pages, keyword, len(items), len(works),
                )
                if len(items) < per_page:
                    break
            except Exception as exc:
                logger.error("Works search failed for '%s': %s", keyword, exc)
                break
        logger.info("Works search '%s' total: %d", keyword, len(works))
        return works

    # ==================================================================
    # Concept/topic search
    # ==================================================================

    def concept_search(self, keyword: str) -> list[dict]:
        logger.info("Concept search: '%s'", keyword)
        try:
            data = self._get(f"{OPENALEX_BASE}/concepts", {"search": keyword, "per_page": 10})
            results = data.get("results", [])
            for c in results[:5]:
                logger.info(
                    "  Concept: '%s' id=%s level=%s works_count=%s",
                    c.get("display_name"), c.get("id"), c.get("level"), c.get("works_count"),
                )
            return results
        except Exception as exc:
            logger.error("concept_search failed for '%s': %s", keyword, exc)
            return []

    def search_topics(self, keyword: str) -> list[dict]:
        logger.info("Topic search: '%s'", keyword)
        try:
            data = self._get(f"{OPENALEX_BASE}/topics", {"search": keyword, "per_page": 10})
            results = data.get("results", [])
            for t in results[:5]:
                logger.info(
                    "  Topic: '%s' id=%s works_count=%s",
                    t.get("display_name"), t.get("id"), t.get("works_count"),
                )
            return results
        except Exception as exc:
            logger.error("topic_search failed for '%s': %s", keyword, exc)
            return []

    # ==================================================================
    # Parse helpers
    # ==================================================================

    def parse_author_to_candidate(self, raw: dict) -> Optional[ResearcherCandidate]:
        """Convert raw author dict to ResearcherCandidate.
        Handles API shape, aggregated shape from retrieve_authors_via_works(),
        and author-search shape (last_known_institutions list).
        """
        openalex_id = raw.get("id", "")
        if not openalex_id:
            return None

        name = raw.get("display_name", "")

        institution_data: dict = {}
        if raw.get("last_known_institution"):
            institution_data = raw["last_known_institution"]
        elif raw.get("last_known_institutions"):
            il = raw["last_known_institutions"]
            if isinstance(il, list) and il:
                institution_data = il[0]

        institution = institution_data.get("display_name", "")
        country_code = institution_data.get("country_code", "")

        raw_concepts = raw.get("x_concepts") or []
        concepts: list[str] = []
        seen_names: set[str] = set()
        for c in raw_concepts:
            dn = c.get("display_name", "")
            if not dn or dn in seen_names:
                continue
            score = c.get("score", 1.0)
            if isinstance(score, (int, float)) and score > 0.1:
                concepts.append(dn)
                seen_names.add(dn)
            elif not isinstance(score, (int, float)):
                concepts.append(dn)
                seen_names.add(dn)

        summary = raw.get("summary_stats") or {}
        h_index = summary.get("h_index", 0)

        # Build recent_papers from aggregated _work_titles/_work_years
        work_titles = raw.get("_work_titles") or []
        work_years = raw.get("_work_years") or []
        recent_papers: list[PaperEvidence] = []
        for i, t in enumerate(work_titles[:20]):
            if t and isinstance(t, str) and t.strip():
                year = work_years[i] if i < len(work_years) else None
                recent_papers.append(PaperEvidence(title=t, year=year))

        works_count = raw.get("works_count", 0)
        cited_by_count = raw.get("cited_by_count", 0)

        # Estimate h_index from aggregated data if not available from summary_stats
        if h_index == 0 and cited_by_count > 0:
            h_index = max(0, int(math.sqrt(cited_by_count / 10)))

        return ResearcherCandidate(
            openalex_id=openalex_id,
            name=name,
            institution=institution,
            country_code=country_code,
            concepts=concepts[:25],
            recent_papers=recent_papers,
            total_works=works_count,
            cited_by_count=cited_by_count,
            h_index=h_index,
            orcid=raw.get("orcid"),
            homepage_url=raw.get("homepage_url"),
        )

    def parse_works_to_papers(self, works: list[dict]) -> list[PaperEvidence]:
        papers: list[PaperEvidence] = []
        for w in works:
            title = w.get("title") or ""
            if not title:
                continue
            year = w.get("publication_year")
            cited = w.get("cited_by_count", 0)
            loc = (w.get("primary_location") or {}).get("source") or {}
            venue = loc.get("display_name", "")
            url = (
                (w.get("primary_location") or {}).get("landing_page_url")
                or w.get("id", "")
            )
            papers.append(
                PaperEvidence(title=title, year=year, url=url, venue=venue, citation_count=cited)
            )
        return papers

    # ==================================================================
    # Compatibility helpers
    # ==================================================================

    def extract_authors_from_works(
        self,
        works: list[dict],
        target_country_codes: set[str],
    ) -> list[dict]:
        seen_ids: set[str] = set()
        authors: list[dict] = []
        for work in works:
            for authorship in work.get("authorships", []):
                if authorship.get("author_position") not in ("first", "last"):
                    continue
                author = authorship.get("author") or {}
                author_id = author.get("id", "")
                if not author_id or author_id in seen_ids:
                    continue
                for inst in authorship.get("institutions", []):
                    cc = inst.get("country_code", "")
                    if not target_country_codes or cc in target_country_codes:
                        seen_ids.add(author_id)
                        authors.append({
                            "id": author_id,
                            "display_name": author.get("display_name", ""),
                            "last_known_institution": inst,
                            "x_concepts": work.get("concepts", []),
                        })
                        break
        logger.info(
            "extract_authors_from_works: %d unique first/last authors from %d works",
            len(authors), len(works),
        )
        return authors

    def extract_all_authors_from_works(self, works: list[dict]) -> list[dict]:
        seen_ids: set[str] = set()
        authors: list[dict] = []
        for work in works:
            for authorship in work.get("authorships", []):
                author = authorship.get("author") or {}
                author_id = author.get("id", "")
                if not author_id or author_id in seen_ids:
                    continue
                seen_ids.add(author_id)
                institutions = authorship.get("institutions") or []
                inst = institutions[0] if institutions else {}
                authors.append({
                    "id": author_id,
                    "display_name": author.get("display_name", ""),
                    "last_known_institution": inst,
                    "x_concepts": work.get("concepts", []),
                })
        logger.info(
            "extract_all_authors_from_works: %d unique authors from %d works",
            len(authors), len(works),
        )
        return authors