import asyncio
from app.services.sp_api_client import sp_api_client
from app.services.validator import evaluate_listing

async def main():
    # ASIN 1: Producto individual o sin variaciones conocidas
    print("--- Probando ASIN 1 ---")
    data_1 = await sp_api_client.fetch_catalog_item("B08JLYZBFS", "US")
    result_1 = evaluate_listing(data_1)
    print(f"ASIN: {result_1.asin} | Reseñas Juntas: {result_1.resenas_juntas} | Notas: {result_1.notas}")

    # ASIN 2: Producto con variaciones
    print("\n--- Probando ASIN 2 ---")
    data_2 = await sp_api_client.fetch_catalog_item("B0FQK4L9ZW", "US")
    result_2 = evaluate_listing(data_2)
    print(f"ASIN: {result_2.asin} | Reseñas Juntas: {result_2.resenas_juntas} | Notas: {result_2.notas}")

    await sp_api_client.close()

if __name__ == "__main__":
    asyncio.run(main())