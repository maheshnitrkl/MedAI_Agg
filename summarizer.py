"""
summarizer.py — Send paper abstracts to Google Gemini and return concise summaries.

Falls back to "Summary unavailable." on any error (missing key, network,
rate-limit, malformed response).
"""

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env once at module level
load_dotenv()

# Maximum abstract length (in characters) sent to the model.
# Roughly ~3 000 tokens — well within flash-tier context limits,
# but prevents accidental mega-prompts.
MAX_ABSTRACT_CHARS = 12_000

FALLBACK_SUMMARY = "Summary unavailable."

SYSTEM_PROMPT = (
    "You are a medical-research summariser. "
    "Given a scientific abstract, produce exactly two sentences in plain language. "
    "Do not use any markdown formatting, bullet points, or special characters. "
    "The summary should be understandable by a non-specialist reader."
)


def _get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets (cloud) first, then fall back to env vars (local)."""
    try:
        import streamlit as st
        value = st.secrets.get(key, "")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()


def summarize_abstract(abstract: str, max_retries: int = 3) -> str:
    """
    Summarise a single abstract using Google Gemini.

    Retries up to `max_retries` times on rate-limit (429) errors with a 35s backoff.
    Returns a plain-text, 2-sentence summary or the fallback string on any failure.
    """
    import time

    api_key = _get_secret("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY is not set — skipping summarisation.")
        return FALLBACK_SUMMARY

    model_name = _get_secret("GEMINI_MODEL", "gemini-2.0-flash")

    # Truncate overly long abstracts as a token-safety measure
    truncated = abstract[:MAX_ABSTRACT_CHARS]
    if len(abstract) > MAX_ABSTRACT_CHARS:
        truncated += " [truncated]"

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=SYSTEM_PROMPT,
        )

        for attempt in range(1, max_retries + 1):
            try:
                response = model.generate_content(truncated)

                # Extract text — guard against empty / blocked responses
                summary_text = (response.text or "").strip()
                if not summary_text:
                    logger.warning("Gemini returned an empty response.")
                    return FALLBACK_SUMMARY

                return summary_text

            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str and attempt < max_retries:
                    wait = 35 * attempt
                    logger.warning(
                        "Rate-limited (attempt %d/%d). Retrying in %ds…",
                        attempt, max_retries, wait,
                    )
                    time.sleep(wait)
                else:
                    raise

    except Exception as exc:
        logger.error("Gemini summarisation failed: %s", exc)
        return FALLBACK_SUMMARY

