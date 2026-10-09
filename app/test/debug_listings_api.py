import asyncio
import json
from app.services.sp_api_client import sp_api_client
from app.core.config import settings


async def test_listings_api():
    asin = "B08P4XRYVV"
    marketplace = "IT"

    print(f"\n🧪 TEST DE LISTINGS ITEMS API — {asin} ({marketplace})\n")

    mkt_code = sp_api_client._normalize(marketplace)
    marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
    region = sp_api_client._get_region(mkt_code)
    base_url = settings.REGION_ENDPOINTS[region]

    token = await sp_api_client.get_lwa_access_token(region)
    headers = {"x-amz-access-token": token, "Content-Type": "application/json"}

    # ========================================================================
    # PASO 1: Obtener sellerId
    # ========================================================================
    print("=" * 78)
    print("PASO 1: GET /sellers/v1/marketplaceParticipations")
    print("=" * 78)

    seller_id = None
    try:
        res = await sp_api_client.client.get(
            f"{base_url}/sellers/v1/marketplaceParticipations",
            headers=headers,
            timeout=15.0,
        )
        print(f"Status HTTP: {res.status_code}")

        if res.status_code == 200:
            data = res.json()
            payload = data.get("payload", [])
            print(f"Participaciones encontradas: {len(payload)}")

            for p in payload:
                mid = p.get("marketplace", {}).get("id")
                sid = p.get("sellerId")
                name = p.get("marketplace", {}).get("name")
                country = p.get("marketplace", {}).get("countryCode")
                print(f"  • Marketplace: {name} ({country}) | ID: {mid} | Seller: {sid}")
                if mid == marketplace_id:
                    seller_id = sid
                    print(f"    ✅ Seller ID para {marketplace}: {seller_id}")
        else:
            print(f"❌ Error: {res.text[:500]}")
    except Exception as e:
        print(f"❌ Excepción: {e}")

    # ========================================================================
    # PASO 2: Obtener SKU desde Catalog Items API (con identifiers)
    # ========================================================================
    print("\n" + "=" * 78)
    print("PASO 2: GET /catalog/2022-04-01/items/{asin}?includedData=identifiers")
    print("=" * 78)

    sku = None
    try:
        res = await sp_api_client.client.get(
            f"{base_url}/catalog/2022-04-01/items/{asin}",
            headers=headers,
            params={
                "marketplaceIds": marketplace_id,
                "includedData": "identifiers,summaries",
            },
            timeout=15.0,
        )
        print(f"Status HTTP: {res.status_code}")

        if res.status_code == 200:
            data = res.json()
            identifiers_list = data.get("identifiers", [])
            print(f"Bloques identifiers: {len(identifiers_list)}")

            for block in identifiers_list:
                for identifier in block.get("identifiers", []):
                    marketplace_identifiers = identifier.get("identifiers", [])
                    for mkt_id in marketplace_identifiers:
                        ident_type = mkt_id.get("identifierType")
                        ident_value = mkt_id.get("identifier")
                        print(f"  • Tipo: {ident_type} | Valor: {ident_value}")
                        if ident_type in ("SKU", "SellerSKU"):
                            sku = ident_value
                            print(f"    ✅ SKU encontrado: {sku}")
        else:
            print(f"❌ Error: {res.text[:500]}")
    except Exception as e:
        print(f"❌ Excepción: {e}")

    # ========================================================================
    # PASO 3: Probar Listings Items API con el SKU
    # ========================================================================
    print("\n" + "=" * 78)
    print("PASO 3: GET /listings/2021-08-01/items/{sellerId}/{sku}")
    print("=" * 78)

    if not seller_id:
        print("⏭️  Saltando: no se obtuvo sellerId en el PASO 1")
    elif not sku:
        print("⏭️  Saltando: no se obtuvo SKU en el PASO 2")
    else:
        try:
            res = await sp_api_client.client.get(
                f"{base_url}/listings/2021-08-01/items/{seller_id}/{sku}",
                headers=headers,
                params={
                    "marketplaceIds": marketplace_id,
                    "includedData": "summaries,attributes,issues,offers,fulfillmentAvailability,relationships,productTypes",
                },
                timeout=15.0,
            )
            print(f"Status HTTP: {res.status_code}")

            if res.status_code == 200:
                data = res.json()
                print(f"\n📋 Respuesta completa:")
                print(json.dumps(data, indent=2)[:3000])
            else:
                print(f"❌ Error: {res.text[:800]}")
        except Exception as e:
            print(f"❌ Excepción: {e}")

    # ========================================================================
    # PASO 4: Probar Product Pricing API (alternativa si Listing falla)
    # ========================================================================
    print("\n" + "=" * 78)
    print("PASO 4: GET /products/pricing/v0/items/{asin}/offers (alternativa)")
    print("=" * 78)

    try:
        res = await sp_api_client.client.get(
            f"{base_url}/products/pricing/v0/items/{asin}/offers",
            headers=headers,
            params={
                "MarketplaceId": marketplace_id,
                "ItemCondition": "New",
            },
            timeout=15.0,
        )
        print(f"Status HTTP: {res.status_code}")

        if res.status_code == 200:
            data = res.json()
            payload = data.get("payload", {})
            summary = payload.get("Summary", {})
            offers = payload.get("Offers", [])

            print(f"\n📊 Resumen:")
            print(f"  • Total ofertas: {summary.get('TotalOfferCount', 0)}")
            print(f"  • BuyBox Prices: {summary.get('BuyBoxPrices', [])}")
            print(f"  • LowestPrices: {summary.get('LowestPrices', [])}")
            print(f"  • Lista de ofertas activas: {len(offers)}")

            if offers:
                print(f"\n📦 Primera oferta:")
                print(json.dumps(offers[0], indent=2)[:1500])
        else:
            print(f"❌ Error: {res.text[:500]}")
    except Exception as e:
        print(f"❌ Excepción: {e}")

    await sp_api_client.close()
    print("\n" + "=" * 78)
    print("TEST COMPLETADO")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(test_listings_api())