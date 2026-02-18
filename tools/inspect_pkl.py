#!/usr/bin/env python3
"""Inspect a .pkl database file to check if it contains valid embeddings.

Does NOT require adalflow — uses raw pickle to load the file.

Usage:
    python tools/inspect_pkl.py <pkl_path_or_repo_name>

Examples:
    python tools/inspect_pkl.py bas_rpc_ai-guardrails
    python tools/inspect_pkl.py ~/.adalflow/databases/bas_rpc_ai-guardrails.pkl
"""
import os
import pickle
import sys


def _vec_len(vec):
    """Return the length of an embedding vector, or 0 if empty/None."""
    if vec is None:
        return 0
    try:
        if isinstance(vec, list):
            return len(vec)
        if hasattr(vec, "shape"):
            return int(vec.shape[-1]) if len(vec.shape) > 0 else 0
        if hasattr(vec, "__len__"):
            return int(len(vec))
    except Exception:
        pass
    return 0


def inspect_pkl(pkl_path: str):
    """Load and inspect a pkl file, printing summary of contents."""

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

    # Raw pickle load
    try:
        with open(pkl_path, "rb") as f:
            db = pickle.load(f)
    except Exception as e:
        print(f"ERROR loading pkl: {e}")
        return

    # Print top-level type and attributes
    print(f"Object type: {type(db).__name__}")
    attrs = [a for a in dir(db) if not a.startswith("_")]
    print(f"Public attrs: {attrs[:20]}")
    print()

    # Try to find transformed documents
    documents = None

    # Method 1: get_transformed_data (adalflow LocalDB)
    if hasattr(db, "get_transformed_data"):
        try:
            documents = db.get_transformed_data(key="split_and_embed")
        except Exception as e:
            print(f"get_transformed_data failed: {e}")

    # Method 2: direct attribute access
    if documents is None and hasattr(db, "transformed_items"):
        items = db.transformed_items
        print(f"transformed_items keys: {list(items.keys()) if isinstance(items, dict) else type(items)}")
        if isinstance(items, dict) and "split_and_embed" in items:
            documents = items["split_and_embed"]

    # Method 3: iterate if it's a list/dict directly
    if documents is None:
        if isinstance(db, list):
            documents = db
        elif isinstance(db, dict):
            print(f"Top-level dict keys: {list(db.keys())[:20]}")

    if documents is None:
        print("Could not find document list in pkl.")
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

        vl = _vec_len(vec)

        if vl == 0:
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
            sizes[vl] = sizes.get(vl, 0) + 1
            if len(sample_texts) < 3:
                sample_texts.append({
                    "index": i,
                    "file_path": file_path,
                    "text_len": len(text) if text else 0,
                    "vec_dim": vl,
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

    # Ensure project root is in path so pickle can resolve api.* classes
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    inspect_pkl(sys.argv[1])
