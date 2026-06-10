"""
FAISS vector store — local, file-based, zero-infra semantic search.

Responsibilities:
- Embed card description text using Google's embedding model
- Build and persist a FAISS flat index
- Search the index for top-k semantically similar cards given a query
"""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from google import genai

load_dotenv()
log = logging.getLogger("vector_store")

# Lazy import FAISS so the app starts even if faiss-cpu isn't installed yet
try:
    import faiss  # type: ignore
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    log.warning("faiss-cpu not installed — vector search unavailable")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMBED_MODEL = "models/gemini-embedding-001"  # Google's available embedding model
EMBED_DIM = 3072                             # Output dimension for gemini-embedding-001
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "faiss.index"
METADATA_PATH = Path(__file__).resolve().parents[1] / "data" / "faiss_metadata.pkl"


# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------

def embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed a list of strings using Google's text-embedding-004.
    Returns a float32 numpy array of shape (len(texts), EMBED_DIM).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    vectors = []

    # Batch in groups of 100 to stay within API rate limits
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
        )
        for emb in response.embeddings:
            vectors.append(emb.values)

    arr = np.array(vectors, dtype=np.float32)
    # L2-normalise so cosine similarity == inner product (enables IndexFlatIP)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    arr = arr / np.where(norms == 0, 1, norms)
    return arr


# ---------------------------------------------------------------------------
# Index lifecycle
# ---------------------------------------------------------------------------

def build_index(cards: list[dict[str, Any]]) -> None:
    """
    Build a FAISS flat index from a list of card dicts (as returned by db.get_all_cards).
    Persists the index and a parallel metadata list (for reconstructing card info from hits).
    """
    if not FAISS_AVAILABLE:
        raise RuntimeError("faiss-cpu is not installed")

    descriptions = [c.get("description", "") or "" for c in cards]
    if not descriptions:
        log.warning("No descriptions found — index not built")
        return

    log.info("Embedding %d card descriptions…", len(descriptions))
    vectors = embed_texts(descriptions)

    index = faiss.IndexFlatIP(EMBED_DIM)  # Inner product on L2-normalised vecs = cosine
    index.add(vectors)

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_PATH))
    METADATA_PATH.write_bytes(pickle.dumps(cards))
    log.info("FAISS index built with %d vectors → %s", index.ntotal, INDEX_PATH)


def load_index() -> tuple["faiss.Index", list[dict[str, Any]]]:
    """Load a previously persisted FAISS index and its metadata."""
    if not FAISS_AVAILABLE:
        raise RuntimeError("faiss-cpu is not installed")
    if not INDEX_PATH.exists() or not METADATA_PATH.exists():
        raise FileNotFoundError(
            f"FAISS index not found at {INDEX_PATH}. Run build_index() first."
        )
    index = faiss.read_index(str(INDEX_PATH))
    metadata: list[dict[str, Any]] = pickle.loads(METADATA_PATH.read_bytes())
    log.info("Loaded FAISS index with %d vectors", index.ntotal)
    return index, metadata


def search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """
    Embed *query* and return the top_k most similar cards from the FAISS index.
    Each result dict is a card row augmented with a 'similarity_score' key.
    """
    index, metadata = load_index()

    query_vec = embed_texts([query])           # shape (1, EMBED_DIM)
    distances, indices = index.search(query_vec, top_k)

    results = []
    for score, idx in zip(distances[0], indices[0]):
        if idx < 0:                            # FAISS returns -1 for padded results
            continue
        card = dict(metadata[idx])
        card["similarity_score"] = float(score)
        results.append(card)

    log.info("Vector search for '%s' returned %d result(s)", query[:60], len(results))
    return results


# ---------------------------------------------------------------------------
# CLI helper: rebuild the index from Supabase
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.db import get_all_cards  # noqa: E402 — conditional import for CLI use

    log.info("Fetching all cards from Supabase to rebuild FAISS index…")
    all_cards = get_all_cards()
    build_index(all_cards)
    log.info("Done.")
