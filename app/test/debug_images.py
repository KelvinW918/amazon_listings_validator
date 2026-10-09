import asyncio
from app.services.sp_api_client import sp_api_client


async def debug_images():
    asin = "B08K7QVC62"
    marketplace = "FR"

    print(f"🔍 Analizando imágenes de {asin} ({marketplace})...\n")

    result = await sp_api_client.fetch_catalog_item(asin=asin, marketplace=marketplace)

    print(f"📸 Total imágenes extraídas: {len(result.image_urls)}\n")

    for i, url in enumerate(result.image_urls, 1):
        print(f"  [{i}] {url}")

    await sp_api_client.close()


if __name__ == "__main__":
    asyncio.run(debug_images())