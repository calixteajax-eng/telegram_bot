"""
Pibliye sou Facebook Page ak Instagram Business atravè Meta Graph API.
PA ap mache toutotan ou pa gen yon Meta Business App apwouve + Page Access
Token (FACEBOOK_PAGE_ACCESS_TOKEN) ak yon Instagram Business Account ID
(INSTAGRAM_BUSINESS_ACCOUNT_ID) konekte ak Page a.

Yo rete DEZAKTIVE (pa rele) jiskaske varyab anviwònman sa yo configure sou
Render - lè sa a, menm kòmand /anonse a ap pibliye sou Facebook + Instagram
otomatikman tou, san chanjman nan kòd la.
"""
import os
import json
import urllib.request
import urllib.error

GRAPH_API_VERSION = "v21.0"
FACEBOOK_PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")
FACEBOOK_PAGE_ACCESS_TOKEN = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
GRAPH_BASE_OVERRIDE = os.environ.get("GRAPH_API_BASE_OVERRIDE")  # pou tès lokal


def _graph_base() -> str:
    return GRAPH_BASE_OVERRIDE or f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def is_facebook_configured() -> bool:
    return bool(FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN)


def is_instagram_configured() -> bool:
    return bool(INSTAGRAM_BUSINESS_ACCOUNT_ID and FACEBOOK_PAGE_ACCESS_TOKEN)


def _post_form(path: str, params: dict) -> dict:
    url = f"{_graph_base()}{path}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph API HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}")


def post_to_facebook_page(message: str, image_url: str | None = None) -> dict:
    if not is_facebook_configured():
        raise RuntimeError("Facebook pa configure (FACEBOOK_PAGE_ID / FACEBOOK_PAGE_ACCESS_TOKEN manke)")
    params = {"message": message, "access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    if image_url:
        params["url"] = image_url
        return _post_form(f"/{FACEBOOK_PAGE_ID}/photos", params)
    return _post_form(f"/{FACEBOOK_PAGE_ID}/feed", params)


def post_to_instagram(caption: str, image_url: str) -> dict:
    if not is_instagram_configured():
        raise RuntimeError("Instagram pa configure (INSTAGRAM_BUSINESS_ACCOUNT_ID manke)")
    if not image_url:
        raise RuntimeError("Instagram mande yon image_url obligatwa")
    created = _post_form(f"/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media", {
        "image_url": image_url, "caption": caption, "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
    })
    container_id = created.get("id")
    if not container_id:
        raise RuntimeError(f"Pa jwenn container_id nan repons Instagram: {created}")
    return _post_form(f"/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media_publish", {
        "creation_id": container_id, "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
    })
