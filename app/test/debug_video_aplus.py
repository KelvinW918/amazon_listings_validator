import asyncio
from app.services.sp_api_client import sp_api_client


CASOS = [
    ("B0BTZW1HPV", "IT", "Funda Asiento Moto XL",        "SÍ video", "SÍ A+ comparativa"),
    ("B07NV1SMB9", "IT", "Pegatinas Calaveras Plata",    "SÍ video", "SÍ A+ comparativa"),
    ("B07T7FYHHT", "FR", "Pack 2 Adhesivos Estrella",    "SÍ video", "SÍ A+ comparativa"),
    ("B0GHPNM7YY", "IT", "Calendario imán vertical",     "sin video", "SÍ A+ comparativa"),
    ("B08P4XRYVV", "IT", "Confezione Lettere Numeri",    "sin video", "SÍ A+ comparativa"),
]


async def debug_video_aplus():
    print("\n" + "=" * 78)
    print("🎬 TEST DE DETECCIÓN DE VIDEO Y A+ COMPARATIVA")
    print("=" * 78)

    resultados = []

    for asin, marketplace, descripcion, video_esp, aplus_esp in CASOS:
        print(f"\n{'─' * 78}")
        print(f"🔍 {asin} ({marketplace}) — {descripcion}")
        print(f"   Esperado: Video={video_esp} | A+={aplus_esp}")
        print(f"{'─' * 78}")

        try:
            r = await sp_api_client.fetch_catalog_item(asin, marketplace)

            video_real = "SÍ video" if r.has_video else ("N/A" if r.has_video is None else "sin video")
            aplus_real = "SÍ A+" if r.has_a_plus else ("N/A" if r.has_a_plus is None else "sin A+")

            video_ok = (video_real == video_esp)
            aplus_ok = (aplus_real == aplus_esp)
            status = "✅" if (video_ok and aplus_ok) else "⚠️"

            print(f"  {status} Video detectado:       {video_real}")
            print(f"     A+ detectado:          {aplus_real}")
            print(f"     A+ productos count:    {r.a_plus_comparison_products_count}")
            print(f"     Variaciones count:     {len(r.variation_asins)}")
            print(f"     Reviews count:         {r.variant_review_counts}")
            print(f"     Imágenes count:        {len(r.image_urls)}")

            resultados.append({
                "asin": asin, "marketplace": marketplace,
                "video_real": video_real, "video_esp": video_esp, "video_ok": video_ok,
                "aplus_real": aplus_real, "aplus_esp": aplus_esp, "aplus_ok": aplus_ok,
            })

        except Exception as e:
            print(f"  ❌ Error procesando {asin}: {e}")
            resultados.append({
                "asin": asin, "marketplace": marketplace,
                "video_real": "ERROR", "video_esp": video_esp, "video_ok": False,
                "aplus_real": "ERROR", "aplus_esp": aplus_esp, "aplus_ok": False,
            })

    print("\n" + "=" * 78)
    print("📊 RESUMEN DEL TEST")
    print("=" * 78)

    total = len(resultados)
    video_ok = sum(1 for r in resultados if r["video_ok"])
    aplus_ok = sum(1 for r in resultados if r["aplus_ok"])
    ambos_ok = sum(1 for r in resultados if r["video_ok"] and r["aplus_ok"])

    print(f"\n{'ASIN':<12} {'Market':<8} {'Video':<20} {'A+':<20} {'Estado'}")
    print("─" * 78)
    for r in resultados:
        v_status = "✅" if r["video_ok"] else "❌"
        a_status = "✅" if r["aplus_ok"] else "❌"
        combo = "✅ OK" if (r["video_ok"] and r["aplus_ok"]) else "⚠️ Revisar"
        print(f"{r['asin']:<12} {r['marketplace']:<8} {v_status} {r['video_real']:<16} {a_status} {r['aplus_real']:<16} {combo}")

    print("\n" + "─" * 78)
    print(f"  Total casos:         {total}")
    print(f"  Video correcto:      {video_ok}/{total}")
    print(f"  A+ correcto:         {aplus_ok}/{total}")
    print(f"  Ambos correctos:     {ambos_ok}/{total}")
    print("─" * 78)

    if ambos_ok == total:
        print("\n🎉 ¡TODOS LOS CASOS PASARON!")
    elif ambos_ok >= total * 0.8:
        print(f"\n✅ Mayoría pasaron ({ambos_ok}/{total}).")
    else:
        print(f"\n⚠️ Muchos fallos ({total - ambos_ok}/{total}).")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_video_aplus())