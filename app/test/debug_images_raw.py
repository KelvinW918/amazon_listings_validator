import asyncio
import json
from app.core.config import settings
from app.services.sp_api_client import sp_api_client


async def debug_images_raw():
    asin = "B08K7QVC62"
    marketplace = "FR"

    print(f"🔍 Analizando imágenes RAW de {asin} ({marketplace})...\n")

    # Obtener el token y hacer la petición cruda
    mkt_code = sp_api_client._normalize(marketplace)
    marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
    region = sp_api_client._get_region(mkt_code)

    raw_data = await sp_api_client._fetch_raw_catalog_item(asin, marketplace_id, region)

    # ============================================================
    # FUENTE 1: data.images[]
    # ============================================================
    print("=" * 70)
    print("📸 FUENTE 1: data.images[] (estructura oficial SP-API)")
    print("=" * 70)

    images_list = raw_data.get("images", [])
    if not images_list:
        print("  ❌ Vacío")
        total_data_images = 0
    else:
        total_data_images = 0
        for idx_container, img_container in enumerate(images_list):
            print(f"\n  📦 Contenedor {idx_container + 1}:")
            marketplace_id_container = img_container.get("marketplaceId", "?")
            print(f"     marketplaceId: {marketplace_id_container}")
            inner_images = img_container.get("images", [])
            for idx_img, img in enumerate(inner_images):
                variant = img.get("variant", "?")
                link = img.get("link", "")
                height = img.get("height", "?")
                width = img.get("width", "?")
                print(f"     [{idx_img + 1}] variant='{variant}' | {width}x{height} | {link}")
                total_data_images += 1

    print(f"\n  🔢 TOTAL data.images[]: {total_data_images}")

    # ============================================================
    # FUENTE 2: attributes.other_product_image_locator_N
    # ============================================================
    print("\n" + "=" * 70)
    print("📸 FUENTE 2: attributes.other_product_image_locator_N")
    print("=" * 70)

    attributes = raw_data.get("attributes", {})
    total_attr_images = 0

    for i in range(1, 10):  # hasta _9 por si acaso
        attr_key = f"other_product_image_locator_{i}"
        attr_val = attributes.get(attr_key)
        if not attr_val:
            continue

        print(f"\n  📦 {attr_key}:")
        if isinstance(attr_val, list):
            for idx, item in enumerate(attr_val):
                if isinstance(item, dict):
                    print(f"     [{idx + 1}] DICT: {json.dumps(item, indent=8, default=str)}")
                else:
                    print(f"     [{idx + 1}] RAW: {item!r}")
                total_attr_images += 1
        else:
            print(f"     RAW: {attr_val!r}")
            total_attr_images += 1

    print(f"\n  🔢 TOTAL attributes locator: {total_attr_images}")

    # ============================================================
    # TODAS las claves que contienen "image" (para no perdernos nada)
    # ============================================================
    print("\n" + "=" * 70)
    print("🔎 TODAS las claves de attributes con 'image' en el nombre")
    print("=" * 70)

    image_keys = [k for k in attributes.keys() if "image" in k.lower()]
    if not image_keys:
        print("  (ninguna)")
    else:
        for k in sorted(image_keys):
            val = attributes[k]
            count = len(val) if isinstance(val, list) else 1
            print(f"  • {k}  ({count} item/s)")

    # ============================================================
    # RESUMEN
    # ============================================================
    print("\n" + "=" * 70)
    print("📊 RESUMEN")
    print("=" * 70)
    print(f"  data.images[]                      : {total_data_images}")
    print(f"  attributes.other_product_image_*   : {total_attr_images}")
    print(f"  (suma bruta sin deduplicar)        : {total_data_images + total_attr_images}")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_images_raw())