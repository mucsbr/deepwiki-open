"""Bounded real-provider QA: one selected source file, isolated output only.

Run in a disposable container with the repository mounted read-only. Does not
change production metadata, indexes, or source. Uses the configured embedder.
"""

import argparse
import contextlib
import io
import json
import logging
from pathlib import Path
import warnings

logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repository, output = Path(args.repository), Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from adalflow.core.types import Document
        from api.data_pipeline import transform_documents_and_save_to_db
        from api.index_state import embedding_spec, index_coverage, source_revision
        from api.agent.repositories import Repository, SourceReader, IndexSearch
        import api.index_builder as builder
        original_getter = builder.get_embedder
        calls = []

        def counted(**kwargs):
            real = original_getter(**kwargs)

            def invoke(input):
                calls.append(len(input) if isinstance(input, list) else 1)
                return real(input=input)

            return invoke

        builder.get_embedder = counted
        revision = source_revision(str(repository))
        assert revision
        document = Document(text=(repository / args.file).read_text(), meta_data={"file_path": args.file})
        index = output / "databases" / f"{repository.name}.pkl"
        database = transform_documents_and_save_to_db([document], str(index), source_revision=revision, source_directory=str(repository))
        complete = index_coverage(database)
        assert complete["status"] == "indexed"
        assert complete["sanitized_data_uris"] >= 1
        assert complete["total_chunks"] >= 2

        # Simulate one missing vector in QA only; keep the actual successful ones.
        chunks = database.get_transformed_data(key="split_and_embed")
        chunks[-1].vector = []
        database.save_state(str(index))
        reader = SourceReader([Repository(args.project, "https://gitlab.example/" + args.project, revision, repository)])
        result = IndexSearch(reader, output).search("document template formatting")
        assert result["matches"]
        assert result["index_coverage"][0]["failed_chunks"] == 1
        assert result["index_coverage"][0]["status"] == "partial"
        calls.clear()
        repaired = transform_documents_and_save_to_db([document], str(index), source_revision=revision,
                                                      source_directory=str(repository), previous=database)
        assert sum(calls) == 1
        assert index_coverage(repaired)["status"] == "indexed"
        report = {"model": embedding_spec()["model"], "file": args.file,
                  "source_chars": len(document.text), "chunks": complete["total_chunks"],
                  "sanitized_data_uris": complete["sanitized_data_uris"],
                  "partial_search_matches": len(result["matches"]), "retry_input_count": sum(calls),
                  "production_data_modified": False}
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
