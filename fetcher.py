"""
fetcher.py — Source-agnostic paper fetching interface.

Currently implements:
    - fetch_arxiv(query, max_results) — arXiv Atom feed via the arXiv API

Designed so a fetch_pubmed() can be added later without touching database.py or app.py.
Each fetch function returns a list of dicts matching the `papers` DB schema
(minus `ai_summary` and `fetched_at`, which are filled downstream).
"""

import logging
from datetime import datetime
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

ARXIV_API_URL = "http://export.arxiv.org/api/query"

DEFAULT_QUERY = (
    'all:"medical imaging" AND (all:"artificial intelligence" OR all:"deep learning")'
)


def fetch_arxiv(
    query: str = DEFAULT_QUERY,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """
    Query the arXiv API and return a list of paper dicts.

    Parameters
    ----------
    query : str
        arXiv search_query string with explicit boolean logic.
    max_results : int
        Maximum number of results to fetch (default 20).
    timeout : int
        HTTP request timeout in seconds (default 15).

    Returns
    -------
    list[dict]
        Each dict contains: id, title, authors, published_date, abstract,
        source, url.  Returns an empty list on any HTTP/parsing error.
    """
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
        # arXiv id link (the abstract page URL) serves as the unique id
        paper_id = entry.get("id", "")

        # Extract the PDF or abstract link
        url = paper_id  # default to the id (which is the abstract URL)
        for link in entry.get("links", []):
            if link.get("type") == "application/pdf":
                url = link.get("href", url)
                break

        # Published date → ISO 8601 YYYY-MM-DD
        published = entry.get("published", "")
        try:
            published_date = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            published_date = published[:10] if len(published) >= 10 else ""

        # Authors — comma-separated string
        authors = ", ".join(
            a.get("name", "") for a in entry.get("authors", [])
        )

        # Title — collapse whitespace that arXiv sometimes inserts
        title = " ".join(entry.get("title", "").split())

        # Abstract — same whitespace cleanup
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


# ---------------------------------------------------------------------------
# PubMed (stub — future implementation)
# ---------------------------------------------------------------------------

def fetch_pubmed(
    query: str = "medical imaging artificial intelligence",
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """
    Placeholder for a future PubMed fetcher.

    Will query the NCBI E-utilities API and return a list of paper dicts
    with the same schema as fetch_arxiv().
    """
    logger.info("PubMed fetcher is not yet implemented.")
    return []
