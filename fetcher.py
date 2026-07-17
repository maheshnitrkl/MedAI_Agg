"""
fetcher.py — Multi-source paper fetching interface.

Implements:
    - fetch_arxiv()            — arXiv Atom feed (preprints, open access)
    - fetch_pubmed()           — NCBI PubMed E-utilities (all journals, OA & subscription)
    - fetch_semantic_scholar() — Semantic Scholar API (all papers, OA & subscription)
    - fetch_openalex()         — OpenAlex API (250M+ works, OA & subscription)
    - fetch_crossref()         — CrossRef API (DOI-registered works from all publishers)
    - fetch_europe_pmc()       — Europe PMC (superset of PubMed, European coverage)
    - fetch_all_sources()      — convenience wrapper that calls selected sources

Each function returns a list of dicts with keys:
    id, title, authors, published_date, abstract, source, url, access_type, journal
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

MEDICAL_AI_KEYWORDS = "medical imaging artificial intelligence deep learning"


# ═══════════════════════════════════════════════════════════════════════════
# arXiv  (all papers are open-access preprints)
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
            "access_type": "Open Access",
            "journal": "arXiv Preprint",
        }
    except Exception as exc:
        logger.warning("Failed to parse arXiv entry: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PubMed  (NCBI E-utilities — indexes ALL biomedical journals, OA & paywalled)
# ═══════════════════════════════════════════════════════════════════════════

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def fetch_pubmed(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 20,
) -> list[dict]:
    """Search PubMed and return paper dicts with abstracts (OA & subscription)."""
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

        # Journal name
        journal_el = art.find("Journal/Title")
        journal = journal_el.text if journal_el is not None else ""

        # Published date
        pub_date = art.find(".//PubDate")
        published_date = ""
        if pub_date is not None:
            year = pub_date.findtext("Year", "")
            month = pub_date.findtext("Month", "01")
            day = pub_date.findtext("Day", "01")
            month_map = {
                "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
                "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
            }
            month = month_map.get(month, month.zfill(2))
            if year:
                published_date = f"{year}-{month}-{day.zfill(2)}"

        # Access type — check for PMC free full text
        pmc_el = article.find(".//PubmedData/ArticleIdList/ArticleId[@IdType='pmc']")
        access_type = "Open Access" if pmc_el is not None else "Subscription"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        return {
            "id": f"pubmed:{pmid}",
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "PubMed",
            "url": url,
            "access_type": access_type,
            "journal": journal,
        }
    except Exception as exc:
        logger.warning("Failed to parse PubMed article: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Semantic Scholar  (indexes papers from ALL publishers, OA & paywalled)
# ═══════════════════════════════════════════════════════════════════════════

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def fetch_semantic_scholar(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Search Semantic Scholar and return paper dicts (OA & subscription)."""
    headers = {"User-Agent": "MedAI-Aggregator/1.0 (research-tool)"}

    for attempt in range(3):
        try:
            resp = requests.get(
                S2_SEARCH_URL,
                params={
                    "query": query,
                    "limit": min(max_results, 100),
                    "fields": "title,authors,abstract,url,publicationDate,externalIds,isOpenAccess,journal",
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
        if not abstract:
            return None

        authors_list = item.get("authors") or []
        authors = ", ".join(a.get("name", "") for a in authors_list if a.get("name"))

        pub_date = item.get("publicationDate") or ""
        published_date = pub_date[:10] if pub_date else ""

        # Journal
        journal_info = item.get("journal") or {}
        journal = journal_info.get("name", "") if isinstance(journal_info, dict) else ""

        # Access type
        is_oa = item.get("isOpenAccess", False)
        access_type = "Open Access" if is_oa else "Subscription"

        # Best URL
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
            "access_type": access_type,
            "journal": journal,
        }
    except Exception as exc:
        logger.warning("Failed to parse Semantic Scholar paper: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# OpenAlex  (250M+ works from ALL publishers — OA & subscription)
# ═══════════════════════════════════════════════════════════════════════════

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def fetch_openalex(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Search OpenAlex and return paper dicts (OA & subscription)."""
    try:
        resp = requests.get(
            OPENALEX_WORKS_URL,
            params={
                "search": query,
                "per_page": min(max_results, 50),
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

        abstract = _reconstruct_openalex_abstract(item.get("abstract_inverted_index"))
        if not abstract:
            return None

        authorships = item.get("authorships") or []
        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in authorships
            if a.get("author", {}).get("display_name")
        )

        published_date = (item.get("publication_date") or "")[:10]

        # Journal / venue
        primary_loc = item.get("primary_location") or {}
        source_info = primary_loc.get("source") or {}
        journal = source_info.get("display_name", "")

        # Access type
        oa_info = item.get("open_access") or {}
        is_oa = oa_info.get("is_oa", False)
        access_type = "Open Access" if is_oa else "Subscription"

        # Best URL: DOI > landing page > OpenAlex page
        doi = item.get("doi") or ""
        if doi:
            url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        else:
            url = primary_loc.get("landing_page_url") or openalex_id

        return {
            "id": openalex_id,
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "OpenAlex",
            "url": url,
            "access_type": access_type,
            "journal": journal,
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
# CrossRef  (100M+ DOI-registered works — Nature, Elsevier, Springer, Wiley,
#            Lancet, NEJM, JAMA, IEEE, ACM, etc. — OA & subscription)
# ═══════════════════════════════════════════════════════════════════════════

CROSSREF_WORKS_URL = "https://api.crossref.org/works"


def fetch_crossref(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 20,
) -> list[dict]:
    """Search CrossRef and return paper dicts (OA & subscription)."""
    try:
        resp = requests.get(
            CROSSREF_WORKS_URL,
            params={
                "query": query,
                "rows": min(max_results, 50),
                "sort": "published",
                "order": "desc",
                "filter": "has-abstract:true",
                "select": "DOI,title,author,abstract,published-print,published-online,"
                          "container-title,link,URL,license",
            },
            headers={
                "User-Agent": "MedAI-Aggregator/1.0 (mailto:research@example.com)",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as exc:
        logger.error("CrossRef fetch failed: %s", exc)
        return []

    papers: list[dict] = []
    for item in items:
        paper = _parse_crossref_item(item)
        if paper:
            papers.append(paper)

    logger.info("Fetched %d papers from CrossRef.", len(papers))
    return papers


def _parse_crossref_item(item: dict) -> Optional[dict]:
    """Parse a CrossRef work item into a paper dict."""
    try:
        doi = item.get("DOI", "")
        if not doi:
            return None

        # Title
        title_list = item.get("title", [])
        title = title_list[0] if title_list else ""
        title = " ".join(title.split())

        # Abstract — CrossRef may include HTML tags; strip them
        abstract = item.get("abstract", "")
        if abstract:
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract)
            abstract = " ".join(abstract.split())
        if not abstract:
            return None

        # Authors
        author_list = item.get("author", [])
        authors = ", ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in author_list
            if a.get("family")
        )

        # Published date
        pub = item.get("published-print") or item.get("published-online") or {}
        date_parts = pub.get("date-parts", [[]])[0]
        if len(date_parts) >= 3:
            published_date = f"{date_parts[0]}-{str(date_parts[1]).zfill(2)}-{str(date_parts[2]).zfill(2)}"
        elif len(date_parts) >= 2:
            published_date = f"{date_parts[0]}-{str(date_parts[1]).zfill(2)}-01"
        elif len(date_parts) >= 1:
            published_date = f"{date_parts[0]}-01-01"
        else:
            published_date = ""

        # Journal
        containers = item.get("container-title", [])
        journal = containers[0] if containers else ""

        # Access type — check for open-access license
        licenses = item.get("license", [])
        is_oa = any(
            "creativecommons" in (lic.get("URL", "") or "").lower()
            for lic in licenses
        )
        access_type = "Open Access" if is_oa else "Subscription"

        url = f"https://doi.org/{doi}"

        return {
            "id": f"crossref:{doi}",
            "title": title,
            "authors": authors,
            "published_date": published_date,
            "abstract": abstract,
            "source": "CrossRef",
            "url": url,
            "access_type": access_type,
            "journal": journal,
        }
    except Exception as exc:
        logger.warning("Failed to parse CrossRef item: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Europe PMC  (superset of PubMed — includes European & international journals,
#              preprints, patents, clinical guidelines — OA & subscription)
# ═══════════════════════════════════════════════════════════════════════════

EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fetch_europe_pmc(
    query: str = MEDICAL_AI_KEYWORDS,
    max_results: int = 20,
    timeout: int = 15,
) -> list[dict]:
    """Search Europe PMC and return paper dicts (OA & subscription)."""
    try:
        resp = requests.get(
            EUROPEPMC_SEARCH_URL,
            params={
                "query": query,
                "format": "json",
                "pageSize": min(max_results, 25),
                "sort": "DATE_CREATED desc",
                "resultType": "core",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("resultList", {}).get("result", [])
    except Exception as exc:
        logger.error("Europe PMC fetch failed: %s", exc)
        return []

    papers: list[dict] = []
    for item in results:
        paper = _parse_europe_pmc(item)
        if paper:
            papers.append(paper)

    logger.info("Fetched %d papers from Europe PMC.", len(papers))
    return papers


def _parse_europe_pmc(item: dict) -> Optional[dict]:
    """Parse a Europe PMC result into a paper dict."""
    try:
        # Unique ID: use DOI if available, else pmid, else pmcid
        doi = item.get("doi", "")
        pmid = item.get("pmid", "")
        pmcid = item.get("pmcid", "")
        paper_id = f"epmc:{doi or pmid or pmcid}"
        if paper_id == "epmc:":
            return None

        title = (item.get("title") or "").strip()
        abstract = (item.get("abstractText") or "").strip()
        if not abstract:
            return None

        # Authors
        author_str = item.get("authorString", "")

        # Published date
        pub_date_str = item.get("firstPublicationDate", "")
        published_date = pub_date_str[:10] if pub_date_str else ""

        # Journal
        journal = item.get("journalTitle", "") or item.get("journalInfo", {}).get("journal", {}).get("title", "")

        # Access type
        is_oa = item.get("isOpenAccess", "N") == "Y"
        access_type = "Open Access" if is_oa else "Subscription"

        # URL: DOI > Europe PMC page
        if doi:
            url = f"https://doi.org/{doi}"
        elif pmid:
            url = f"https://europepmc.org/article/med/{pmid}"
        else:
            url = f"https://europepmc.org/article/pmc/{pmcid}"

        return {
            "id": paper_id,
            "title": title,
            "authors": author_str,
            "published_date": published_date,
            "abstract": abstract,
            "source": "Europe PMC",
            "url": url,
            "access_type": access_type,
            "journal": journal,
        }
    except Exception as exc:
        logger.warning("Failed to parse Europe PMC result: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: fetch from all sources at once
# ═══════════════════════════════════════════════════════════════════════════

ALL_SOURCES = {
    "arXiv": fetch_arxiv,
    "PubMed": fetch_pubmed,
    "CrossRef": fetch_crossref,
    "Europe PMC": fetch_europe_pmc,
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
