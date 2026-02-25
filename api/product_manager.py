"""
Product Manager

Manages product definitions that aggregate multiple repositories into
logical units. Products are persisted as JSON at
~/.adalflow/metadata/products.json
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from adalflow.utils import get_adalflow_default_root_path

logger = logging.getLogger(__name__)

METADATA_DIR = os.path.join(get_adalflow_default_root_path(), "metadata")
PRODUCTS_FILE = os.path.join(METADATA_DIR, "products.json")


def _ensure_dir() -> None:
    os.makedirs(METADATA_DIR, exist_ok=True)


def _load() -> dict:
    _ensure_dir()
    if not os.path.exists(PRODUCTS_FILE):
        return {"products": {}}
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load products: %s", e)
        return {"products": {}}


def _save(data: dict) -> None:
    _ensure_dir()
    try:
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to save products: %s", e)


def load_products() -> dict:
    """Return the full products data structure."""
    return _load()


def save_products(data: dict) -> None:
    """Persist the full products data structure."""
    _save(data)


def list_products() -> List[dict]:
    """Return all products as a list of dicts with their IDs."""
    data = _load()
    result = []
    for pid, pdata in data.get("products", {}).items():
        result.append({"id": pid, **pdata})
    return result


def get_product(product_id: str) -> Optional[dict]:
    """Return a single product by ID, or None if not found."""
    data = _load()
    pdata = data.get("products", {}).get(product_id)
    if pdata is None:
        return None
    return {"id": product_id, **pdata}


def create_product(
    product_id: str,
    name: str,
    description: str,
    repos: List[str],
) -> dict:
    """Create a new product. Raises ValueError if ID already exists."""
    data = _load()
    if product_id in data.get("products", {}):
        raise ValueError(f"Product '{product_id}' already exists")
    product = {
        "name": name,
        "description": description,
        "repos": repos,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data.setdefault("products", {})[product_id] = product
    _save(data)
    return {"id": product_id, **product}


def update_product(product_id: str, **kwargs) -> dict:
    """Update an existing product. Accepts name, description, repos.
    Raises ValueError if product not found."""
    data = _load()
    products = data.get("products", {})
    if product_id not in products:
        raise ValueError(f"Product '{product_id}' not found")
    product = products[product_id]
    for key in ("name", "description", "repos"):
        if key in kwargs and kwargs[key] is not None:
            product[key] = kwargs[key]
    product["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(data)
    return {"id": product_id, **product}


def delete_product(product_id: str) -> None:
    """Delete a product. Raises ValueError if not found."""
    data = _load()
    products = data.get("products", {})
    if product_id not in products:
        raise ValueError(f"Product '{product_id}' not found")
    del products[product_id]
    _save(data)
