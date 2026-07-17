"""
database.py — SQLite connection, table creation, insert (with dedup), and fetch functions.

Table: papers
Stores arXiv (and future PubMed) paper metadata plus AI-generated summaries.

Design: each public function opens and closes its own connection so there are
no cross-thread issues when used inside Streamlit.
"""

import sqlite3
import logging
from typing import Optional

DB_PATH = "papers.db"

logger = logging.getLogger(__name__)


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a new SQLite connection with row-factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Create the database and ensure the schema exists."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                authors         TEXT,
                published_date  TEXT,
                abstract        TEXT,
                source          TEXT,
                ai_summary      TEXT,
                url             TEXT,
                access_type     TEXT DEFAULT 'Unknown',
                journal         TEXT DEFAULT '',
                fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Migrate older databases that lack the new columns
        for col, default in [("access_type", "'Unknown'"), ("journal", "''")]:
            try:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        logger.info("Database initialised at %s", db_path)
    finally:
        conn.close()


def paper_exists(paper_id: str, db_path: str = DB_PATH) -> bool:
    """Check whether a paper with the given id is already stored."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,))
        return cursor.fetchone() is not None
    finally:
        conn.close()


def insert_paper(paper: dict, db_path: str = DB_PATH) -> bool:
    """
    Insert a single paper dict into the database.

    Uses INSERT OR IGNORE so duplicate ids are silently skipped.
    Returns True if a row was actually inserted, False if it already existed.

    Expected keys in `paper`:
        id, title, authors, published_date, abstract, source, ai_summary, url
    """
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO papers
                (id, title, authors, published_date, abstract, source, ai_summary, url)
            VALUES
                (:id, :title, :authors, :published_date, :abstract, :source, :ai_summary, :url)
            """,
            paper,
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def insert_papers(papers: list[dict], db_path: str = DB_PATH) -> int:
    """
    Bulk-insert a list of paper dicts.

    Returns the number of *new* rows actually inserted (duplicates are skipped).
    """
    inserted = 0
    conn = _connect(db_path)
    try:
        for paper in papers:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO papers
                    (id, title, authors, published_date, abstract, source, ai_summary, url, access_type, journal)
                VALUES
                    (:id, :title, :authors, :published_date, :abstract, :source, :ai_summary, :url, :access_type, :journal)
                """,
                paper,
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def fetch_all_papers(
    source: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Retrieve papers in reverse chronological order (by published_date).

    If `source` is provided (e.g. "arXiv" or "PubMed"), filter to that source only.
    Returns a list of dicts.
    """
    conn = _connect(db_path)
    try:
        if source:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE source = ? ORDER BY published_date DESC",
                (source,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM papers ORDER BY published_date DESC"
            )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_existing_ids(db_path: str = DB_PATH) -> set[str]:
    """Return the set of all paper ids currently in the database."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT id FROM papers")
        return {row["id"] for row in cursor.fetchall()}
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(f"Database ready at '{DB_PATH}'. Tables created.")
