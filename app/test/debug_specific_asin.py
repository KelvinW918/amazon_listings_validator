import asyncio
from app.services.sp_api_client import sp_api_client


async def debug_specific():
    asin = "B0DQWHGVGQ"
    marketplace = "IT"

    print("\n" + "=" * 78)
    print(f"🔍 DIAGNÓSTICO COMPLETO — {asin} ({marketplace})")
    print("=" * 78)

    # ========================================================================
    # RESULTADO COMPLETO DEL MOTOR
    # ========================================================================
    print("\n📊 RESULTADO DEL MOTOR DE VALIDACIÓN:")
    print("─" * 78)

    try:
        r = await sp_api_client.fetch_catalog_item(asin, marketplace)

        print(f"  ASIN:                      {r.asin}")
        print(f"  Marketplace:               {r.marketplace}")
        print(f"  Activo:                    {r.is_active}")
        print(f"  Título:                    {r.title[:60]}{'...' if len(r.title) > 60 else ''}")
        print(f"  Título length:             {len(r.title)} chars")
        print(f"  Bullet Points:             {len(r.bullet_points)} bullets")
        print(f"  Variaciones:               {len(r.variation_asins)} ({r.variation_asins})")
        print(f"  Imágenes:                  {len(r.image_urls)}")
        print(f"  Video:                     {r.has_video}")
        print(f"  A+ Comparativa:            {r.has_a_plus}")
        print(f"  A+ comparativa count:      {r.a_plus_comparison_products_count}")
        print(f"  A+ tiene PVP:              {r.a_plus_has_price}")
        print(f"  A+ tiene Reseñas:          {r.a_plus_has_reviews}")
        print(f"  Reviews por variante:      {r.variant_review_counts}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        await sp_api_client.close()
        return

    # ========================================================================
    # DETALLE DEL A+ CONTENT (SP-API)
    # ========================================================================
    print("\n" + "=" * 78)
    print("🔬 A+ CONTENT — DATOS CRUDOS DE SP-API")
    print("=" * 78)

    mkt_code = sp_api_client._normalize(marketplace)
    marketplace_id = sp_api_client.settings.MARKETPLACE_IDS.get(mkt_code) if hasattr(sp_api_client, 'settings') else None

    # Acceso a settings de forma correcta
    from app.core.config import settings
    marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
    region = sp_api_client._get_region(mkt_code)

    aplus_data = await sp_api_client._fetch_aplus_content(asin, marketplace_id, region)

    print(f"\n  has_a_plus (SP-API):       {aplus_data['has_a_plus']}")
    print(f"  comparison_products_count: {aplus_data['a_plus_comparison_products_count']}")
    print(f"  has_price (SP-API):        {aplus_data['a_plus_has_price']}")
    print(f"  has_reviews (SP-API):      {aplus_data['a_plus_has_reviews']}")
    print(f"  has_video_in_aplus:        {aplus_data['has_video_in_aplus']}")

    # ========================================================================
    # DETALLE DEL SCRAPE (fallback)
    # ========================================================================
    print("\n" + "=" * 78)
    print("🌐 FALLBACK SCRAPE — HTML PÚBLICO")
    print("=" * 78)

    fallback = await sp_api_client._fallback_public_page_check(asin, mkt_code)

    print(f"\n  scrape_success:            {fallback['scrape_success']}")
    print(f"  has_video:                 {fallback['has_video']}")
    print(f"  has_a_plus:                {fallback['has_a_plus']}")
    print(f"  a_plus_comparison_count:   {fallback['a_plus_comparison_products_count']}")
    print(f"  review_count:              {fallback['review_count']}")

    # ========================================================================
    # RESULTADO FINAL ESPERADO
    # ========================================================================
    print("\n" + "=" * 78)
    print("🎯 VEREDICTO FINAL")
    print("=" * 78)

    if r.has_a_plus:
        print("  ✅ A+ Comparativa: OK — El parser detecta A+ content")
    elif r.has_a_plus is False:
        print("  ❌ A+ Comparativa: NOK — El parser NO detecta A+ content")
    else:
        print("  ⚠️ A+ Comparativa: N/A — No se pudo verificar")

    print("=" * 78)

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_specific())