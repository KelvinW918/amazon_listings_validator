import contextlib
import logging
from fastapi import FastAPI

from app.core.config import settings
from app.api.v1.validator import router as validator_router
from app.services.sp_api_client import sp_api_client

# =========================================================================
# CONFIGURACIÓN DE LOGGING GLOBAL
# =========================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:\t%(asctime)s - %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("main")


# =========================================================================
# HELPERS DE PRE-WARM
# =========================================================================
async def _prewarm_curl_cffi() -> bool:
    """
    Carga la librería nativa de curl_cffi en memoria.
    Evita el cold-init de ~5-15s en la primera petición real.
    """
    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession() as session:
            # Petición ligera a un dominio externo para forzar la carga
            # de libcurl-impersonate y el fingerprint TLS de Chrome.
            await session.get(
                "https://www.google.com",
                impersonate="chrome124",
                timeout=5.0,
            )
        logger.info("🔥 [LIFESPAN] curl_cffi cargado OK")
        return True
    except Exception as e:
        logger.warning(f"⚠️ [LIFESPAN] Fallo al pre-calentar curl_cffi: {e}")
        return False


async def _prewarm_lwa_tokens() -> None:
    """
    Pre-obtiene los tokens LWA de las regiones configuradas.
    Evita el +2-3s de la primera petición real a SP-API.
    Solo intenta regiones que tienen refresh token configurado.
    """
    regions_to_warm = []

    if settings.SP_API_REFRESH_TOKEN_NA:
        regions_to_warm.append("NA")
    if settings.SP_API_REFRESH_TOKEN_EU:
        regions_to_warm.append("EU")
    if settings.SP_API_REFRESH_TOKEN_FE:
        regions_to_warm.append("FE")

    # Si no hay tokens regionales, usar el fallback global
    if not regions_to_warm and settings.SP_API_REFRESH_TOKEN:
        regions_to_warm = ["EU"]  # el fallback sirve para cualquier región

    if not regions_to_warm:
        logger.warning("⚠️ [LIFESPAN] No hay refresh tokens configurados para pre-calentar")
        return

    for region in regions_to_warm:
        try:
            await sp_api_client.get_lwa_access_token(region)
            logger.info(f"🔥 [LIFESPAN] Token LWA {region} cacheado OK")
        except Exception as e:
            logger.warning(f"⚠️ [LIFESPAN] No se pudo pre-obtener token {region}: {e}")


async def _prewarm_sp_api_tls() -> None:
    """
    Establece el handshake TLS contra los endpoints de SP-API.
    Reduce la latencia de la primera petición real ~1-2s.
    """
    try:
        import httpx

        endpoints_to_warm = [
            settings.REGION_ENDPOINTS.get("EU"),
            settings.REGION_ENDPOINTS.get("NA"),
        ]

        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in endpoints_to_warm:
                if not url:
                    continue
                try:
                    # 403/404 esperado: solo queremos el handshake TLS
                    await client.get(url)
                except Exception:
                    pass

        logger.info("🔥 [LIFESPAN] TLS handshake SP-API OK")
    except Exception as e:
        logger.warning(f"⚠️ [LIFESPAN] Fallo al pre-calentar TLS SP-API: {e}")


# =========================================================================
# LIFESPAN (STARTUP + SHUTDOWN)
# =========================================================================
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------------------------------------------------------------------
    # STARTUP: Pre-warm del servidor
    # ---------------------------------------------------------------------
    logger.info("🔥 [LIFESPAN] Iniciando pre-warm del servidor...")

    await _prewarm_curl_cffi()
    await _prewarm_lwa_tokens()
    await _prewarm_sp_api_tls()

    logger.info("🔥 [LIFESPAN] Pre-warm completado. Servidor listo para recibir tráfico.")

    yield

    # ---------------------------------------------------------------------
    # SHUTDOWN: Cierre seguro de conexiones
    # ---------------------------------------------------------------------
    logger.info("💤 [LIFESPAN] Cerrando servidor...")
    await sp_api_client.close()
    logger.info("💤 [LIFESPAN] Servidor cerrado correctamente.")


# =========================================================================
# INSTANCIA DE FASTAPI
# =========================================================================
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Endpoint Validador
app.include_router(validator_router, prefix=settings.API_V1_STR)


# =========================================================================
# HEALTH CHECK
# =========================================================================
@app.get("/health", tags=["Health Check"])
async def health_check():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }