import asyncio
from curl_cffi.requests import AsyncSession


async def check():
    print("🌐 Verificando estado de la IP contra Amazon...\n")

    async with AsyncSession() as s:
        try:
            r = await s.get(
                "https://www.amazon.es/dp/B0F298DTW1",
                impersonate="chrome124",
                timeout=15.0,
            )
            size = len(r.text)

            print(f"Status HTTP: {r.status_code}")
            print(f"Tamaño HTML: {size:,} chars")

            # Detectar CAPTCHA explícito
            has_captcha = (
                "api-services-support@amazon.com" in r.text
                or "validateCaptcha" in r.text
                or "Robot Check" in r.text
            )

            if has_captcha:
                print("\n❌ IP BLOQUEADA — Amazon devolvió CAPTCHA explícito")
                print("   Espera 2-4 horas antes de volver a intentar")
                return

            if size > 500_000:
                print("\n✅ IP FRESCA — podemos testear scrape")
                print(f"   HTML completo recibido ({size:,} chars)")
            else:
                print("\n❌ IP BLOQUEADA — HTML demasiado pequeño")
                print(f"   Esperado >500K chars, recibido {size:,}")
                print("   Espera 2-4 horas antes de volver a intentar")

        except Exception as e:
            print(f"\n⚠️ Error de red: {e}")
            print("   Puede ser un problema de conexión o IP bloqueada")


if __name__ == "__main__":
    asyncio.run(check())