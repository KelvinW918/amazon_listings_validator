import asyncio
from app.services.sp_api_client import sp_api_client


CASOS = [
    ("B08P4XRYVV", "IT", "INACTIVO (Attualmente non disponibile)"),
    ("B0FDH2CSB3", "DE", "ACTIVO (tiene 366 reviews)"),
    ("B0F298DTW1", "ES", "ACTIVO (validado anteriormente)"),
    ("B0GHPNM7YY", "IT", "ACTIVO (validado anteriormente)"),
    ("B0DQWHGVGQ", "IT", "ACTIVO (validado anteriormente)"),
]


async def debug_buyability():
    print("\n" + "=" * 78)
    print("🛒 TEST DE DETECCIÓN DE ESTADO (BUYABILITY)")
    print("=" * 78)

    resultados = []

    for asin, marketplace, esperado in CASOS:
        print(f"\n{'─' * 78}")
        print(f"🔍 {asin} ({marketplace})")
        print(f"   Esperado: {esperado}")
        print(f"{'─' * 78}")

        try:
            r = await sp_api_client.fetch_catalog_item(asin, marketplace)

            if r.is_active is True:
                estado = "Activo"
            elif r.is_active is False:
                estado = "Inactivo"
            else:
                estado = "N/A"

            print(f"  → is_active:       {r.is_active}")
            print(f"  → Estado:          {estado}")

            # Comparar con lo esperado
            if "INACTIVO" in esperado and r.is_active is False:
                ok = True
                print(f"  ✅ CORRECTO")
            elif "ACTIVO" in esperado and r.is_active is True:
                ok = True
                print(f"  ✅ CORRECTO")
            else:
                ok = False
                print(f"  ⚠️  Revisar — no coincide con lo esperado")

            resultados.append({
                "asin": asin,
                "marketplace": marketplace,
                "is_active": r.is_active,
                "estado": estado,
                "esperado": esperado,
                "ok": ok,
            })

        except Exception as e:
            print(f"  ❌ Error: {e}")
            resultados.append({
                "asin": asin,
                "marketplace": marketplace,
                "is_active": None,
                "estado": "ERROR",
                "esperado": esperado,
                "ok": False,
            })

    # ========================================================================
    # RESUMEN
    # ========================================================================
    print("\n" + "=" * 78)
    print("📊 RESUMEN DEL TEST")
    print("=" * 78)

    print(f"\n{'ASIN':<12} {'Market':<8} {'is_active':<11} {'Estado':<10} {'Esperado':<40} {'OK'}")
    print("─" * 78)

    for r in resultados:
        print(
            f"{r['asin']:<12} {r['marketplace']:<8} "
            f"{str(r['is_active']):<11} {r['estado']:<10} "
            f"{r['esperado']:<40} {'✅' if r['ok'] else '❌'}"
        )

    total = len(resultados)
    correctos = sum(1 for r in resultados if r["ok"])

    print("\n" + "─" * 78)
    print(f"  Total casos:     {total}")
    print(f"  Correctos:       {correctos}/{total}")
    print("─" * 78)

    if correctos == total:
        print("\n🎉 ¡TODOS LOS CASOS PASARON! Product Pricing API funciona perfecto.")
    elif correctos >= total * 0.8:
        print(f"\n✅ Mayoría pasaron ({correctos}/{total}). Revisar los fallos específicos.")
    else:
        print(f"\n⚠️  Muchos fallos ({total - correctos}/{total}). Revisar el parser con más detalle.")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_buyability())