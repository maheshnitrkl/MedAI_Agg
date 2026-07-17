"""
fetcher.py — Multi-source paper fetching interface.

Implements:
    - fetch_arxiv()            — arXiv Atom feed
    - fetch_pubmed()           — NCBI PubMed E-utilities (esearch + efetch)
    - fetch_semantic_scholar() — Semantic Scholar Academic Graph API
    - fetch_openalex()         — OpenAlex REST API
    - fetch_all_sources()      — convenience wrapper that calls all of the above

Each function returns a list of dicts with keys:
    id, title, authors, published_date, abstract, source, url
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

MEDICAL_AI_KEYWORDS = "medical imaging artificial intelligence deep learning"

# ═══════════════════════════════════════════════════════════════════════════
# arXiv
# ═══════════════════════════════════════════════════════════════════════════

ARXIV_API_URL = "http://export.arxiv.org/api/query"

DEFAULT_ARXIV_QUERY = (
    'all:"medical imaging" AND (all:"artificial intelligence" OR all:"deep learning")'
)


def fetch_arxiv(
    query: str = DEFAULT_ARXIV_QUERY,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Query the arXiv API and return a list of paper dicts."""
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    try:
        response = requests.get(ARXIV_API_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("arXiv fetch failed: %s", exc)
        return []

    feed = feedparser.parse(response.text)

    papers: list[dict] = []
    for entry in feed.entries:
        paper = _parse_arxiv_entry(entry)
        if paper:
            papers.append(paper)

    logger.info("Fetched %d papers from arXiv.", len(papers))
    return papers


def _parse_arxiv_entry(entry) -> Optional[dict]:
    """Convert a single feedparser entry into a paper dict."""
    try:
        paper_id = entry.get("id", "")
        url = paper_id
        for link in entry.get("links", []):
            if link.get("type") == "application/pdf":
                url = link.get("href", url)
                break

        published = entry.get("published", "")
        try:
            published_date = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            published_date = published[:10] if len(published) >= 10 else ""

        authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
        title = " ".join(entry.get("title", "").split())
        abstract = " ".join(entry.get("summary", "").split())

        return {
            "id": paper_id,
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "arXiv",
            "url": url,
        }
    except Exception as exc:
        logger.warning("Failed to parse arXiv entry: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PubMed  (NCBI E-utilities — free, no API key required for moderate use)
# ═══════════════════════════════════════════════════════════════════════════

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def fetch_pubmed(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 20,
) -> list[dict]:
    """Search PubMed and return paper dicts with abstracts."""
    # Step 1 — search for PMIDs
    try:
        search_resp = requests.get(
            PUBMED_ESEARCH,
            params={
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "sort": "date",
                "retmode": "json",
            },
            timeout=timeout,
        )
        search_resp.raise_for_status()
        id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])
    except Exception as exc:
        logger.error("PubMed search failed: %s", exc)
        return []

    if not id_list:
        logger.info("PubMed returned 0 results.")
        return []

    # Step 2 — fetch full records as XML
    try:
        fetch_resp = requests.get(
            PUBMED_EFETCH,
            params={
                "db": "pubmed",
                "id": ",".join(id_list),
                "rettype": "xml",
                "retmode": "xml",
            },
            timeout=timeout,
        )
        fetch_resp.raise_for_status()
    except Exception as exc:
        logger.error("PubMed fetch failed: %s", exc)
        return []

    papers: list[dict] = []
    try:
        root = ET.fromstring(fetch_resp.content)
        for article in root.findall(".//PubmedArticle"):
            paper = _parse_pubmed_article(article)
            if paper:
                papers.append(paper)
    except ET.ParseError as exc:
        logger.error("PubMed XML parse error: %s", exc)

    logger.info("Fetched %d papers from PubMed.", len(papers))
    return papers


def _parse_pubmed_article(article) -> Optional[dict]:
    """Parse a PubmedArticle XML element into a paper dict."""
    try:
        medline = article.find(".//MedlineCitation")
        if medline is None:
            return None

        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else ""
        if not pmid:
            return None

        art = medline.find(".//Article")
        if art is None:
            return None

        # Title
        title_el = art.find("ArticleTitle")
        title = title_el.text if title_el is not None else ""
        # Some titles contain inline XML — flatten
        if title_el is not None and not title:
            title = "".join(title_el.itertext())
        title = " ".join(title.split())

        # Abstract
        abstract_parts: list[str] = []
        abstract_el = art.find("Abstract")
        if abstract_el is not None:
            for text_el in abstract_el.findall("AbstractText"):
                label = text_el.get("Label", "")
                text_content = "".join(text_el.itertext()).strip()
                if label:
                    abstract_parts.append(f"{label}: {text_content}")
                else:
                    abstract_parts.append(text_content)
        abstract = " ".join(abstract_parts) if abstract_parts else ""

        # Authors
        author_list = art.find("AuthorList")
        authors_strs: list[str] = []
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = author.findtext("LastName", "")
                fore = author.findtext("ForeName", "")
                name = f"{fore} {last}".strip()
                if name:
                    authors_strs.append(name)
        authors = ", ".join(authors_strs)

        # Published date
        pub_date = art.find(".//PubDate")
        published_date = ""
        if pub_date is not None:
            year = pub_date.findtext("Year", "")
            month = pub_date.findtext("Month", "01")
            day = pub_date.findtext("Day", "01")
            # Month might be a name like "Jan"
            month_map = {
                "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
                "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
            }
            month = month_map.get(month, month.zfill(2))
            if year:
                published_date = f"{year}-{month}-{day.zfill(2)}"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        return {
            "id": f"pubmed:{pmid}",
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "PubMed",
            "url": url,
        }
    except Exception as exc:
        logger.warning("Failed to parse PubMed article: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Semantic Scholar  (free, no key required — 100 req / 5 min)
# ═══════════════════════════════════════════════════════════════════════════

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def fetch_semantic_scholar(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Search Semantic Scholar and return paper dicts."""
    import time

    headers = {"User-Agent": "MedAI-Aggregator/1.0 (research-tool)"}

    for attempt in range(3):
        try:
            resp = requests.get(
                S2_SEARCH_URL,
                params={
                    "query": query,
                    "limit": min(max_results, 100),
                    "fields": "title,authors,abstract,url,publicationDate,externalIds",
                },
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning("Semantic Scholar rate-limited. Retrying in %ds…", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json().get("data", [])
            break
        except Exception as exc:
            logger.error("Semantic Scholar fetch failed: %s", exc)
            return []
    else:
        logger.error("Semantic Scholar: exhausted retries due to rate limiting.")
        return []

    papers: list[dict] = []
    for item in data:
        paper = _parse_s2_paper(item)
        if paper:
            papers.append(paper)

    logger.info("Fetched %d papers from Semantic Scholar.", len(papers))
    return papers


def _parse_s2_paper(item: dict) -> Optional[dict]:
    """Parse a Semantic Scholar result into a paper dict."""
    try:
        paper_id = item.get("paperId", "")
        if not paper_id:
            return None

        title = (item.get("title") or "").strip()
        abstract = (item.get("abstract") or "").strip()

        # Skip papers without abstracts
        if not abstract:
            return None

        # Authors
        authors_list = item.get("authors") or []
        authors = ", ".join(a.get("name", "") for a in authors_list if a.get("name"))

        # Published date
        pub_date = item.get("publicationDate") or ""
        published_date = pub_date[:10] if pub_date else ""

        # Best URL: DOI > Semantic Scholar page
        ext_ids = item.get("externalIds") or {}
        doi = ext_ids.get("DOI", "")
        if doi:
            url = f"https://doi.org/{doi}"
        else:
            url = item.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"

        return {
            "id": f"s2:{paper_id}",
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "Semantic Scholar",
            "url": url,
        }
    except Exception as exc:
        logger.warning("Failed to parse Semantic Scholar paper: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# OpenAlex  (free, no key required — polite pool with email in User-Agent)
# ═══════════════════════════════════════════════════════════════════════════

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def fetch_openalex(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Search OpenAlex and return paper dicts."""
    try:
        resp = requests.get(
            OPENALEX_WORKS_URL,
            params={
                "search": query,
                "per_page": min(max_results, 50),  # API max per page is 200
                "sort": "publication_date:desc",
                "filter": "has_abstract:true",
            },
            headers={"User-Agent": "MedAI-Aggregator/1.0 (research-tool)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        logger.error("OpenAlex fetch failed: %s", exc)
        return []

    papers: list[dict] = []
    for item in results:
        paper = _parse_openalex_work(item)
        if paper:
            papers.append(paper)

    logger.info("Fetched %d papers from OpenAlex.", len(papers))
    return papers


def _parse_openalex_work(item: dict) -> Optional[dict]:
    """Parse an OpenAlex work into a paper dict."""
    try:
        openalex_id = item.get("id", "")
        if not openalex_id:
            return None

        title = (item.get("title") or "").strip()

        # OpenAlex stores abstract as an inverted index — reconstruct it
        abstract = _reconstruct_openalex_abstract(item.get("abstract_inverted_index"))
        if not abstract:
            return None

        # Authors
        authorships = item.get("authorships") or []
        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in authorships
            if a.get("author", {}).get("display_name")
        )

        # Published date
        published_date = (item.get("publication_date") or "")[:10]

        # Best URL: DOI > landing page > OpenAlex page
        doi = item.get("doi") or ""
        if doi:
            url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        else:
            primary = item.get("primary_location") or {}
            url = primary.get("landing_page_url") or openalex_id

        return {
            "id": openalex_id,
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "OpenAlex",
            "url": url,
        }
    except Exception as exc:
        logger.warning("Failed to parse OpenAlex work: %s", exc)
        return None


def _reconstruct_openalex_abstract(inverted_index: Optional[dict]) -> str:
    """Rebuild plain text from OpenAlex's inverted-index abstract format."""
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: fetch from all sources at once
# ═══════════════════════════════════════════════════════════════════════════

ALL_SOURCES = {
    "arXiv": fetch_arxiv,
    "PubMed": fetch_pubmed,
    "Semantic Scholar": fetch_semantic_scholar,
    "OpenAlex": fetch_openalex,
}


def fetch_all_sources(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results_per_source: int = 10,
    sources: Optional[list[str]] = None,
) -> list[dict]:
    """
    Fetch papers from multiple sources.

    Parameters
    ----------
    query : str
        Search terms (plain keywords — arXiv gets its own boolean query).
    max_results_per_source : int
        How many papers to request from each source.
    sources : list[str] | None
        Which sources to query. None means all.

    Returns
    -------
    list[dict]
        Combined list of paper dicts from all requested sources.
    """
    selected = sources or list(ALL_SOURCES.keys())
    combined: list[dict] = []

    for source_name in selected:
        fetcher = ALL_SOURCES.get(source_name)
        if fetcher is None:
            logger.warning("Unknown source: %s", source_name)
            continue

        # arXiv uses its own boolean query syntax
        if source_name == "arXiv":
            papers = fetcher(query=DEFAULT_ARXIV_QUERY, max_results=max_results_per_source)
        else:
            papers = fetcher(query=query, max_results=max_results_per_source)

        combined.extend(papers)

    logger.info("Total papers fetched from %d sources: %d", len(selected), len(combined))
    return combined
