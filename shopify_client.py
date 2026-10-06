"""Kliyan Shopify REST senp - jwenn pwodui pa SKU, chanje pri, mete foto."""
import os
import urllib.request
import urllib.error
import json

SHOP = os.environ.get("SHOPIFY_SHOP", "elmondocomprafacil.myshopify.com")
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-07")
TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
BASE_URL_OVERRIDE = os.environ.get("SHOPIFY_BASE_URL_OVERRIDE")  # pou tès lokal sèlman


def _base_url() -> str:
    return BASE_URL_OVERRIDE or f"https://{SHOP}/admin/api/{API_VERSION}"


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Shopify HTTP {e.code}: {body}")


def find_product_and_variant_by_sku(sku: str) -> tuple[dict, dict] | None:
    """Chèche pami tout pwodui yo (limit 250, ase pou yon ti katalòg) pou jwenn
    (product, variant) ki gen SKU sa a. Retounen None si pa jwenn."""
    data = _request("GET", "/products.json?limit=250")
    for product in data.get("products", []):
        for variant in product.get("variants", []):
            if variant.get("sku") == sku:
                return product, variant
    return None


def update_price(variant_id: int, new_price: float) -> dict:
    payload = {"variant": {"id": variant_id, "price": f"{new_price:.2f}"}}
    return _request("PUT", f"/variants/{variant_id}.json", payload)


def attach_image_url(product_id: int, image_url: str) -> dict:
    payload = {"image": {"src": image_url}}
    return _request("POST", f"/products/{product_id}/images.json", payload)


def list_all_tracked_skus(skus: list[str]) -> list[dict]:
    """Retounen non + pri aktyèl pou chak SKU nan lis la (pou /list)."""
    data = _request("GET", "/products.json?limit=250")
    found = {}
    for product in data.get("products", []):
        for variant in product.get("variants", []):
            if variant.get("sku") in skus:
                found[variant["sku"]] = {
                    "title": product.get("title"), "price": variant.get("price"),
                    "sku": variant.get("sku"),
                }
    return [found.get(sku, {"sku": sku, "title": "PA JWENN", "price": None}) for sku in skus]
