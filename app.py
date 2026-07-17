"""
app.py — Streamlit dashboard for the Medical AI Research Aggregator.

Displays papers from the local SQLite database and provides a sidebar
button to fetch and store new papers from arXiv.
"""

import logging
import streamlit as st

from database import init_db, get_existing_ids, insert_papers, fetch_all_papers
from fetcher import fetch_arxiv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Medical AI Research Aggregator",
    page_icon="🧬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Custom CSS for a polished look
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* ── Global ─────────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* ── Sidebar ────────────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0c29, #302b63, #24243e);
    }
    section[data-testid="stSidebar"] * {
        color: #e0e0e0 !important;
    }

    /* ── Paper cards ────────────────────────────────────────── */
    .paper-card {
        background: linear-gradient(135deg, #1e1e2f 0%, #2a2a40 100%);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 14px;
        padding: 1.6rem 1.8rem;
        margin-bottom: 1.2rem;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .paper-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 30px rgba(100, 100, 255, 0.12);
    }

    .paper-title {
        font-size: 1.15rem;
        font-weight: 600;
        color: #a78bfa;
        margin-bottom: 0.3rem;
        line-height: 1.4;
    }
    .paper-meta {
        font-size: 0.82rem;
        color: #9ca3af;
        margin-bottom: 0.7rem;
    }
    .paper-summary {
        font-size: 0.95rem;
        color: #d1d5db;
        line-height: 1.65;
    }

    /* ── Header banner ──────────────────────────────────────── */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 16px;
        padding: 2rem 2.5rem;
        margin-bottom: 1.8rem;
        text-align: center;
    }
    .main-header h1 {
        color: #ffffff !important;
        font-size: 2rem;
        font-weight: 700;
        margin: 0;
    }
    .main-header p {
        color: rgba(255, 255, 255, 0.82);
        font-size: 1rem;
        margin: 0.5rem 0 0 0;
    }

    /* ── Stat pills ─────────────────────────────────────────── */
    .stat-pill {
        display: inline-block;
        background: rgba(167, 139, 250, 0.12);
        border: 1px solid rgba(167, 139, 250, 0.25);
        border-radius: 999px;
        padding: 0.35rem 1rem;
        font-size: 0.85rem;
        color: #a78bfa;
        margin-right: 0.6rem;
        margin-bottom: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Database init (creates tables if needed; each DB call opens its own connection)
# ---------------------------------------------------------------------------
init_db()

# ---------------------------------------------------------------------------
# Sidebar — fetch controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Controls")
    st.markdown("---")

    query = st.text_input(
        "Search query",
        value='all:"medical imaging" AND (all:"artificial intelligence" OR all:"deep learning")',
        help="arXiv search_query with boolean operators",
    )
    max_results = st.slider("Max papers to fetch", 5, 50, 20)

    fetch_clicked = st.button("🚀 Fetch Latest Papers", use_container_width=True)

    st.markdown("---")
    st.markdown(
        "<small style='color:#888'>Data source: arXiv API</small>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Pipeline: fetch → filter-new → insert
# ---------------------------------------------------------------------------
if fetch_clicked:
    with st.spinner("Fetching papers from arXiv…"):
        raw_papers = fetch_arxiv(query=query, max_results=max_results)

    if not raw_papers:
        st.warning("No papers returned from arXiv. Try again later or adjust the query.")
    else:
        # Filter out papers already in the DB
        existing_ids = get_existing_ids()
        new_papers = [p for p in raw_papers if p["id"] not in existing_ids]

        if not new_papers:
            st.info("All fetched papers are already in the database — nothing new to add.")
        else:
            # Set ai_summary to None for all new papers
            for paper in new_papers:
                paper["ai_summary"] = None

            inserted = insert_papers(new_papers)
            st.success(f"Added {inserted} new paper{'s' if inserted != 1 else ''}.")
            logger.info("Inserted %d new papers.", inserted)

# ---------------------------------------------------------------------------
# Main content — header
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="main-header">
        <h1>🧬 Medical AI Research Aggregator</h1>
        <p>Latest papers on medical imaging &amp; artificial intelligence from arXiv</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Load papers from DB
# ---------------------------------------------------------------------------
papers = fetch_all_papers()

if not papers:
    st.info(
        "📭 No papers in the database yet. "
        "Click **Fetch Latest Papers** in the sidebar to get started!"
    )
else:
    # Stat pills
    arxiv_count = sum(1 for p in papers if p["source"] == "arXiv")
    pubmed_count = sum(1 for p in papers if p["source"] == "PubMed")
    stats_html = f'<span class="stat-pill">📄 {len(papers)} papers</span>'
    if arxiv_count:
        stats_html += f'<span class="stat-pill">arXiv: {arxiv_count}</span>'
    if pubmed_count:
        stats_html += f'<span class="stat-pill">PubMed: {pubmed_count}</span>'
    st.markdown(stats_html, unsafe_allow_html=True)

    st.markdown("")  # spacer

    # Paper cards
    for paper in papers:
        st.markdown(
            f"""
            <div class="paper-card">
                <div class="paper-title">{paper["title"]}</div>
                <div class="paper-meta">
                    📅 {paper["published_date"]}  &nbsp;•&nbsp;  📚 {paper["source"]}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("Read Full Abstract"):
            st.write(paper["abstract"])
            st.markdown(f"[🔗 View paper]({paper['url']})")
