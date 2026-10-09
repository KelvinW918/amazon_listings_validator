import asyncio
import unicodedata
from app.services.sp_api_client import sp_api_client


async def debug_title():
    asin = "B0FH4TJLSM"
    marketplace = "ES"

    print(f"🔍 Analizando título de {asin} ({marketplace})...\n")

    result = await sp_api_client.fetch_catalog_item(asin=asin, marketplace=marketplace)

    title_raw = result.title
    title_stripped = title_raw.strip()
    title_normalized = unicodedata.normalize("NFC", title_stripped)

    print(f"📄 Título RAW        : {title_raw!r}")
    print(f"📏 Longitud RAW      : {len(title_raw)}")

    print(f"\n📄 Título STRIP      : {title_stripped!r}")
    print(f"📏 Longitud STRIP    : {len(title_stripped)}")

    print(f"\n📄 Título NORMALIZED : {title_normalized!r}")
    print(f"📏 Longitud NORM     : {len(title_normalized)}")

    print(f"\n🔬 Caracteres (con códigos Unicode):")
    for i, c in enumerate(title_normalized):
        try:
            name = unicodedata.name(c, "?")
        except Exception:
            name = "?"
        print(f"  [{i:3d}] {c!r} -> U+{ord(c):04X} ({name})")

    print(f"\n🔍 Caracteres no-imprimibles o raros:")
    weird_count = 0
    for i, c in enumerate(title_normalized):
        if ord(c) > 0xFFFF or (ord(c) < 0x20 and c not in "\n\r\t"):
            print(f"  [{i:3d}] {c!r} -> U+{ord(c):04X}")
            weird_count += 1
    if weird_count == 0:
        print("  Ninguno (todos son caracteres normales)")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_title())