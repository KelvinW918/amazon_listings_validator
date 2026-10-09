import asyncio
import json
from app.core.config import settings
from app.services.sp_api_client import sp_api_client


async def debug_variations():
    asin = "B08P4XRYVV"
    marketplace = "IT"

    print(f"🔍 Analizando variaciones de {asin} ({marketplace})...\n")

    mkt_code = sp_api_client._normalize(marketplace)
    marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
    region = sp_api_client._get_region(mkt_code)

    raw_data = await sp_api_client._fetch_raw_catalog_item(asin, marketplace_id, region)

    # ============================================================
    # RELATIONSHIPS RAW
    # ============================================================
    print("=" * 70)
    print("🔗 RELATIONSHIPS RAW")
    print("=" * 70)

    relationships = raw_data.get("relationships", [])
    if not relationships:
        print("  ❌ Sin relationships")
    else:
        print(json.dumps(relationships, indent=2, default=str))

    # ============================================================
    # PADRE
    # ============================================================
    parent_asin = sp_api_client._extract_parent_asin(raw_data)
    print(f"\n👨 Parent ASIN detectado: {parent_asin}")

    # ============================================================
    # HIJOS DIRECTOS (si es padre)
    # ============================================================
    direct_children = sp_api_client._extract_child_asins(raw_data)
    print(f"👶 Hijos directos del payload: {direct_children}")

    # ============================================================
    # FAMILIA COMPLETA
    # ============================================================
    print("\n" + "=" * 70)
    print("👪 FAMILIA COMPLETA RESUELTA")
    print("=" * 70)

    family = await sp_api_client._resolve_variation_family(
        asin=asin,
        raw_data=raw_data,
        parent_asin=parent_asin,
        marketplace_id=marketplace_id,
        region=region
    )
    print(f"  Total ASINs en familia: {len(family)}")
    for a in family:
        print(f"  • {a}")

    # ============================================================
    # SI HAY PADRE, VER QUÉ CONTIENE
    # ============================================================
    if parent_asin:
        print("\n" + "=" * 70)
        print(f"🔍 CONTENIDO DEL PADRE {parent_asin}")
        print("=" * 70)

        try:
            parent_data = await sp_api_client._fetch_raw_catalog_item(
                parent_asin, marketplace_id, region
            )
            parent_children = sp_api_client._extract_child_asins(parent_data)
            print(f"  Hijos detectados en el padre: {len(parent_children)}")
            for c in parent_children:
                print(f"  • {c}")

            print(f"\n  Relationships del padre:")
            print(json.dumps(parent_data.get("relationships", []), indent=2, default=str))
        except Exception as e:
            print(f"  ❌ Error consultando padre: {e}")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_variations())