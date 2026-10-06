"""Jenere tèks pwomosyon (espayòl) pou yon pwodui, ak pri reyèl li."""

STORE_NAME = "Casa Mia Store"
STORE_URL = "https://elmondocomprafacil.myshopify.com"


def generate_ad_caption(title: str, price_clp: str, sku: str) -> str:
    try:
        price_int = int(float(price_clp))
        price_fmt = f"${price_int:,}".replace(",", ".")
    except (ValueError, TypeError):
        price_fmt = f"${price_clp}"

    return (
        f"🔥 OFERTA {STORE_NAME} 🔥\n\n"
        f"{title}\n"
        f"Precio: {price_fmt} CLP\n\n"
        f"Corre antes que se agote - stock limitado.\n"
        f"Compra aqui: {STORE_URL}\n\n"
        f"#{sku.replace('-', '')} #CasaMiaStore #OfertaChile"
    )
