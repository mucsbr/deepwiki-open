#!/usr/bin/env python3
"""Inspect a .pkl database file to check if it contains valid embeddings.

Usage:
    python -m tools.inspect_pkl <pkl_path_or_repo_name>

Examples:
    python -m tools.inspect_pkl bas_rpc_ai-guardrails
    python -m tools.inspect_pkl ~/.adalflow/databases/bas_rpc_ai-guardrails.pkl
"""
import os
import sys

def inspect_pkl(pkl_path: str):
    """Load and inspect a pkl file, printing summary of contents."""
    from adalflow.database.localdb import LocalDB

    if not os.path.exists(pkl_path):
        # Try as a repo name
        root = os.path.expanduser(os.path.join("~", ".adalflow"))
        alt = os.path.join(root, "databases", f"{pkl_path}.pkl")
        if os.path.exists(alt):
            pkl_path = alt
        else:
            print(f"ERROR: File not found: {pkl_path}")
            print(f"  Also tried: {alt}")
            # List available pkl files
            db_dir = os.path.join(root, "databases")
            if os.path.isdir(db_dir):
                pkls = [f for f in os.listdir(db_dir) if f.endswith(".pkl")]
                print(f"\nAvailable pkl files in {db_dir}:")
                for p in sorted(pkls):
                    size = os.path.getsize(os.path.join(db_dir, p))
                    print(f"  {p}  ({size:,} bytes)")
            return

    file_size = os.path.getsize(pkl_path)
    print(f"File: {pkl_path}")
    print(f"Size: {file_size:,} bytes")
    print()

    try:
        db = LocalDB.load_state(pkl_path)
    except Exception as e:
        print(f"ERROR loading pkl: {e}")
        return

    # Try to get transformed data
    documents = db.get_transformed_data(key="split_and_embed")
    if documents is None:
        print("No 'split_and_embed' key found in database.")
        # Check what keys exist
        if hasattr(db, 'transformed_items'):
            print(f"Available keys: {list(db.transformed_items.keys())}")
        return

    total = len(documents)
    print(f"Total documents: {total}")
    print()

    # Analyze embeddings
    empty_count = 0
    non_empty_count = 0
    sizes = {}
    sample_texts = []
    sample_empty = []

    for i, doc in enumerate(documents):
        vec = getattr(doc, "vector", None)
        text = getattr(doc, "text", "")
        meta = getattr(doc, "meta_data", {})
        file_path = meta.get("file_path", "?") if isinstance(meta, dict) else "?"

        vec_len = 0
        if vec is not None:
            try:
                if isinstance(vec, list):
                    vec_len = len(vec)
                elif hasattr(vec, "shape"):
                    vec_len = vec.shape[-1] if len(vec.shape) > 0 else 0
                elif hasattr(vec, "__len__"):
                    vec_len = len(vec)
            except Exception:
                pass

        if vec_len == 0:
            empty_count += 1
            if len(sample_empty) < 3:
                sample_empty.append({
                    "index": i,
                    "file_path": file_path,
                    "text_len": len(text) if text else 0,
                    "text_preview": (text[:80] + "...") if text and len(text) > 80 else text,
                    "vector_type": type(vec).__name__ if vec is not None else "None",
                })
        else:
            non_empty_count += 1
            sizes[vec_len] = sizes.get(vec_len, 0) + 1
            if len(sample_texts) < 3:
                sample_texts.append({
                    "index": i,
                    "file_path": file_path,
                    "text_len": len(text) if text else 0,
                    "vec_dim": vec_len,
                })

    print(f"Embeddings: {non_empty_count} non-empty, {empty_count} empty")
    if sizes:
        print(f"Embedding dimensions: {sizes}")
    print()

    if sample_texts:
        print("Sample documents WITH embeddings:")
        for s in sample_texts:
            print(f"  [{s['index']}] file={s['file_path']}  text_len={s['text_len']}  vec_dim={s['vec_dim']}")
        print()

    if sample_empty:
        print("Sample documents WITHOUT embeddings:")
        for s in sample_empty:
            print(f"  [{s['index']}] file={s['file_path']}  text_len={s['text_len']}  vector_type={s['vector_type']}")
            if s.get("text_preview"):
                print(f"           text: {s['text_preview']}")
        print()

    # Conclusion
    if non_empty_count == 0:
        print("CONCLUSION: pkl has NO valid embeddings. Embedding step likely failed.")
        print("  The documents exist but vectors are empty/None.")
    elif empty_count > 0:
        print(f"CONCLUSION: pkl has PARTIAL embeddings ({non_empty_count}/{total}).")
        print(f"  {empty_count} documents are missing embeddings.")
    else:
        print(f"CONCLUSION: pkl is healthy. All {total} documents have embeddings.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # Ensure project root is in path for imports
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

    inspect_pkl(sys.argv[1])