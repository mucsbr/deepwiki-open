"""
Multi-Repository RAG

Provides cross-repository retrieval by merging documents from multiple
repository embedding databases into a single FAISS index.
"""

import logging
from typing import List, Optional

from adalflow.components.retriever.faiss_retriever import FAISSRetriever

from api.config import configs
from api.data_pipeline import DatabaseManager
from api.rag import RAG
from api.tools.embedder import get_embedder

logger = logging.getLogger(__name__)


class MultiRepoRAG(RAG):
    """RAG that spans multiple repositories.

    Usage::

        rag = MultiRepoRAG(provider="google", model="gemini-2.5-flash")
        rag.prepare_multi_retriever(
            repo_urls=["https://gitlab.com/group/project-a", "https://gitlab.com/group/project-b"],
            repo_type="gitlab",
        )
        docs = rag("What is the authentication flow?")
    """

    def prepare_multi_retriever(
        self,
        repo_urls: List[str],
        repo_type: str = "gitlab",
        access_token: Optional[str] = None,
        excluded_dirs: Optional[List[str]] = None,
        excluded_files: Optional[List[str]] = None,
        included_dirs: Optional[List[str]] = None,
        included_files: Optional[List[str]] = None,
    ) -> None:
        """Load embeddings from multiple repos and build a unified FAISS index.

        Each document's ``meta_data`` is tagged with ``source_repo`` so that
        retrieved results can be traced back to the originating repository.
        """
        self.initialize_db_manager()
        all_docs = []

        for repo_url in repo_urls:
            try:
                db_manager = DatabaseManager()
                docs = db_manager.prepare_database(
                    repo_url,
                    repo_type,
                    access_token,
                    embedder_type=self.embedder_type,
                    excluded_dirs=excluded_dirs,
                    excluded_files=excluded_files,
                    included_dirs=included_dirs,
                    included_files=included_files,
                )
                # Tag each document with source repo
                for doc in docs:
                    if not hasattr(doc, "meta_data") or doc.meta_data is None:
                        doc.meta_data = {}
                    doc.meta_data["source_repo"] = repo_url

                all_docs.extend(docs)
                logger.info(
                    "Loaded %d documents from %s", len(docs), repo_url
                )
            except Exception as e:
                logger.warning(
                    "Failed to load documents from %s: %s (skipping)", repo_url, e
                )
                continue

        if not all_docs:
            raise ValueError(
                "No valid documents found across any of the provided repositories."
            )

        # Validate and filter embeddings (reuse parent logic)
        self.transformed_docs = self._validate_and_filter_embeddings(all_docs)

        if not self.transformed_docs:
            raise ValueError(
                "No valid documents with embeddings found across repositories."
            )

        logger.info(
            "Building unified FAISS index with %d documents from %d repos",
            len(self.transformed_docs),
            len(repo_urls),
        )

        retrieve_embedder = (
            self.query_embedder if self.is_ollama_embedder else self.embedder
        )
        self.retriever = FAISSRetriever(
            **configs["retriever"],
            embedder=retrieve_embedder,
            documents=self.transformed_docs,
            document_map_func=lambda doc: doc.vector,
        )
        logger.info("Multi-repo FAISS retriever created successfully")
