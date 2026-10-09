import asyncio
from app.services.sp_api_client import sp_api_client
from app.services.validator import BULLET_FORMAT_REGEX
from app.core.config import settings


async def debug_bullets():
    asin = "B08P4XRYVV"
    marketplace = "IT"

    print(f"🔍 Analizando bullets de {asin} ({marketplace})...\n")

    r = await sp_api_client.fetch_catalog_item(asin, marketplace)

    print(f"Total bullets: {len(r.bullet_points)}\n")
    print("=" * 78)

    for i, bullet in enumerate(r.bullet_points, 1):
        # Primeros 3 chars con codepoints
        prefix = bullet[:5]
        codepoints = " ".join(f"U+{ord(c):04X}" for c in prefix)

        # Test contra el regex
        match = BULLET_FORMAT_REGEX.match(bullet)

        # También verificar longitud
        length = len(bullet)

        status = "✅" if match else "❌"
        length_status = "✅" if length <= settings.MAX_BULLET_CHARS else "❌"

        print(f"\n{status} B{i} ({length} chars, {length_status} ≤{settings.MAX_BULLET_CHARS})")
        print(f"   Prefijo: {prefix!r}")
        print(f"   Codepoints: {codepoints}")
        print(f"   Bullet completo: {bullet[:120]}{'...' if len(bullet) > 120 else ''}")

        if not match:
            print(f"   ⚠️ NO matchea el regex actual")
            # Intentar identificar dónde falla
            for j in range(1, min(30, len(bullet) + 1)):
                if BULLET_FORMAT_REGEX.match(bullet[:j]):
                    print(f"   → Matchea hasta posición {j}: {bullet[:j]!r}")
                    break
            else:
                print(f"   → No matchea ninguna subcadena inicial")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_bullets())