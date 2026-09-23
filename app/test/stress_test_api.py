import asyncio
import time
import httpx

URL = "http://127.0.0.1:8000/api/v1/validator/evaluate"
TOTAL_REQUESTS = 500
CONCURRENCY = 50  # 50 peticiones simultáneas

SAMPLE_PAYLOAD = {
    "asin": "B0STRESSTEST",
    "marketplace": "ES",
    "is_active": True,
    "title": "Título de prueba para stress testing con longitud adecuada 65 chars",
    "bullet_points": [f"🔥 BENEFICIO {i}: Descripción de alta densidad" for i in "ABCDE"],
    "variation_asins": ["B0V1", "B0V2"],
    "image_urls": [f"https://img.com/{i}.jpg" for i in range(8)],
    "has_video": True,
    "has_a_plus": True,
    "a_plus_comparison_products_count": 4,
    "a_plus_has_price": True,
    "a_plus_has_reviews": True,
    "variant_review_counts": [50, 50],
}

async def send_request(client, sem):
    async with sem:
        try:
            start = time.perf_counter()
            res = await client.post(URL, json=SAMPLE_PAYLOAD)
            elapsed = time.perf_counter() - start
            return res.status_code, elapsed
        except Exception as e:
            return 500, 0.0

async def main():
    print(f"🚀 Iniciando test de estrés: {TOTAL_REQUESTS} reqs | Concurrencia: {CONCURRENCY}")
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=10.0) as client:
        start_time = time.perf_counter()
        tasks = [send_request(client, sem) for _ in range(TOTAL_REQUESTS)]
        results = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - start_time

    status_codes = [r[0] for r in results]
    latencies = [r[1] for r in results if r[0] == 200]

    print("\n" + "="*40)
    print("📊 RESULTADOS DEL TEST DE ESTRÉS DE API")
    print("="*40)
    print(f"Peticiones Exitosas (200 OK): {status_codes.count(200)} / {TOTAL_REQUESTS}")
    print(f"Peticiones Fallidas:          {TOTAL_REQUESTS - status_codes.count(200)}")
    print(f"Tiempo Total:                 {total_time:.2f} segundos")
    print(f"RPS (Throughput):             {TOTAL_REQUESTS / total_time:.2f} req/sec")
    if latencies:
        print(f"Latencia Promedio:            {sum(latencies)/len(latencies)*1000:.2f} ms")
        print(f"Latencia Máxima:              {max(latencies)*1000:.2f} ms")

if __name__ == "__main__":
    asyncio.run(main())