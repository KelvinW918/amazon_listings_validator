import asyncio
import logging
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.listing import ListingValidationInput, N8nListingOutput
from app.services.validator import evaluate_listing
from app.services.sp_api_client import (
    sp_api_client,
    DEFAULT_POLICY,
    AGGRESSIVE_POLICY,
)
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/validator", tags=["Validation Engine"])

# Semáforo para prevenir Rate Limits (429) en Amazon SP-API (Máx. 5 peticiones concurrentes)
SP_API_SEMAPHORE = asyncio.Semaphore(5)


class BatchItemInput(BaseModel):
    asin: str = Field(..., description="ASIN de Amazon")
    marketplace: str = Field(default="ES", description="Código de Marketplace (ej. ES, US, DE)")


class BatchEvalRequest(BaseModel):
    items: List[BatchItemInput]


def _build_error_fallback(asin: str, status_msg: str, error_detail: str) -> N8nListingOutput:
    """Helper interno para construir una respuesta 1:1 cuando ocurre un fallo de infraestructura."""
    logger.warning(f"⚠️ Generando fallback para ASIN {asin} por estado: '{status_msg}' | Detalle: {error_detail}")
    return N8nListingOutput(
        ASIN=asin,
        **{
            "Listing Status": status_msg,
            "Titulo": "N/A",
            "Bullet Points": "N/A",
            "Variaciones": "N/A",
            "Imagenes Correctas": "N/A",
            "Video": "N/A",
            "A+ Comparativa": "N/A",
            "Reseñas Juntas": "N/A",
            "Notas": f"Fallo de transporte / SP-API: {error_detail[:120]}",
            "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    )


# =============================================================================
# HELPER — ESTADO DE NEGOCIO (para la capa resiliente de segunda pasada)
# =============================================================================
# Diferencia respecto a _build_error_fallback:
#   - _build_error_fallback      → "hubo un fallo técnico" (flujo principal)
#   - _build_business_state_fallback → "el producto no existe/no está autorizado"
#     (segunda pasada, es un estado final, no un error)
# =============================================================================
def _build_business_state_fallback(
    asin: str,
    marketplace: str,
    status: str,
    detail: str,
) -> N8nListingOutput:
    """Construye un output válido cuando el ASIN no es procesable (estado de negocio)."""
    logger.warning(
        f"🚫 [BUSINESS STATE] ASIN={asin} ({marketplace}) | "
        f"Status='{status}' | Detail='{detail[:100]}'"
    )
    return N8nListingOutput(
        ASIN=asin,
        **{
            "Listing Status": status,
            "Titulo": "N/A",
            "Bullet Points": "N/A",
            "Variaciones": "N/A",
            "Imagenes Correctas": "N/A",
            "Video": "N/A",
            "A+ Comparativa": "N/A",
            "Reseñas Juntas": "N/A",
            "Notas": f"[{marketplace}] {detail[:160]}",
            "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    )


@router.post("/evaluate", response_model=N8nListingOutput)
async def evaluate_listing_endpoint(payload: ListingValidationInput):
    """Recibe el payload manual/customizado del ASIN y evalúa determinísticamente las reglas SOP."""
    logger.info(f"📥 [POST /evaluate] Recibida petición manual para ASIN: {payload.asin} ({payload.marketplace})")
    logger.debug(f"Payload entrante completo: {payload.model_dump()}")

    result = evaluate_listing(payload)

    if hasattr(result, "to_n8n_output"):
        final_output = result.to_n8n_output(asin=payload.asin)
    else:
        final_output = result

    logger.info(f"✅ [POST /evaluate] Evaluación manual completada para ASIN: {payload.asin}")
    return final_output


@router.get("/fetch-and-evaluate/{marketplace}/{asin}", response_model=N8nListingOutput)
async def fetch_and_evaluate_listing(marketplace: str, asin: str):
    print(f"\n>>> ¡PETICIÓN RECIBIDA! ASIN: {asin}, Marketplace: {marketplace} <<<\n", flush=True)
    """
    Extrae los datos del ASIN desde SP-API y evalúa automáticamente las reglas SOP.

    - Re-lanza errores 400/401/403 (Marketplace no soportado, auth LWA) para activar la rama de error en n8n.
    - Captura errores 5xx/timeouts/red para responder con fallback controlado de tipo N8nListingOutput.
    """
    logger.info(f"🔍 [GET /fetch-and-evaluate] Iniciando consulta en Amazon SP-API -> ASIN: {asin} | Marketplace: {marketplace}")

    try:
        input_data = await sp_api_client.fetch_catalog_item(asin, marketplace)
        print(f">>> DATA EXTRAÍDA: {input_data.model_dump()}\n", flush=True)

        # Logs formateados para inspeccionar la extracción real en consola
        logger.info(f"📦 [SP-API EXTRACCIÓN EXITOSA] Datos crudos obtenidos para {asin}:")
        logger.info(f"  ├─ Título ({len(input_data.title or '')} chars): '{input_data.title[:50]}...'" if input_data.title else "  ├─ Título: [VACÍO / NONE]")
        logger.info(f"  ├─ Bullet Points: {len(input_data.bullet_points or [])} ítems -> {input_data.bullet_points}")
        logger.info(f"  ├─ Imágenes Extraídas: {len(input_data.image_urls or [])} URLs")
        logger.info(f"  └─ Variaciones Detectadas: {len(input_data.variation_asins or [])} ASINs")
        logger.debug(f"Dump Pydantic extraído de SP-API: {input_data.model_dump()}")

        result = evaluate_listing(input_data)

        if hasattr(result, "to_n8n_output"):
            final_output = result.to_n8n_output(asin=asin)
        else:
            final_output = result

        logger.info(f"📊 [EVALUACIÓN COMPLETADA] ASIN: {asin} | Status: {final_output.listing_status if hasattr(final_output, 'listing_status') else 'OK'}")
        logger.info(f"📝 [NOTAS REGISTRADAS]: {final_output.dict().get('Notas', 'Sin notas')}")

        return final_output

    except HTTPException as e:
        if e.status_code in (400, 401, 403):
            logger.error(f"❌ Error de configuración/autenticación [{e.status_code}] para ASIN {asin} ({marketplace}): {e.detail}")
            raise e

        logger.warning(f"⚠️ Fallback activado por HTTP {e.status_code} para ASIN {asin} ({marketplace}): {e.detail}")
        return _build_error_fallback(asin, "Error SP-API", f"HTTP {e.status_code}: {e.detail}")

    except Exception as e:
        logger.error(f"💥 Error no controlado de red/transporte para ASIN {asin} ({marketplace}): {str(e)}", exc_info=True)
        return _build_error_fallback(asin, "Error Red", str(e))


@router.post("/batch-fetch-and-evaluate", response_model=List[N8nListingOutput])
async def batch_fetch_and_evaluate(payload: BatchEvalRequest):
    """
    Procesa un lote masivo de ASINs de forma asíncrona controlada.
    Utiliza un semáforo de concurrencia para evitar bloqueos por Rate Limit (HTTP 429) en Amazon SP-API.
    Garantiza una respuesta 1:1 para cada ítem solicitado.
    """
    total_items = len(payload.items)
    logger.info(f"🚀 [POST /batch-fetch-and-evaluate] Iniciando procesamiento en lote para {total_items} ASINs...")

    async def process_single_with_limit(item: BatchItemInput) -> N8nListingOutput:
        async with SP_API_SEMAPHORE:
            logger.info(f"⏳ [BATCH-ITEM] Procesando ASIN: {item.asin} ({item.marketplace})")
            try:
                res = await fetch_and_evaluate_listing(item.marketplace, item.asin)
                logger.info(f"✅ [BATCH-ITEM] Completado exitosamente: {item.asin}")
                return res
            except HTTPException as e:
                logger.error(f"❌ [BATCH-ITEM] Error HTTP {e.status_code} en ASIN {item.asin}: {e.detail}")
                return _build_error_fallback(item.asin, "Error Config", f"HTTP {e.status_code}: {e.detail}")
            except Exception as e:
                logger.error(f"❌ [BATCH-ITEM] Error no controlado en ASIN {item.asin}: {str(e)}")
                return _build_error_fallback(item.asin, "Error Desconocido", str(e))

    results = await asyncio.gather(*(process_single_with_limit(item) for item in payload.items))
    logger.info(f"🏁 [BATCH FINALIZADO] Se procesaron {len(results)} de {total_items} ASINs correctamente.")
    return list(results)


@router.get("/warmup")
async def warmup():
    """
    Endpoint de pre-calentamiento real.
    Ejecuta una consulta completa contra un ASIN de bajo riesgo
    para forzar: LWA + SP-API + curl_cffi + Amazon scraping.
    """
    logger.info("🔥 [WARMUP] Iniciando pre-calentamiento completo...")
    try:
        # Usa un ASIN de tu propio catálogo (bajo riesgo, siempre activo)
        result = await sp_api_client.fetch_catalog_item(
            asin="B0F298DTW1",  # ASIN conocido que funciona
            marketplace="ES"
        )
        logger.info(f"🔥 [WARMUP] OK: {result.asin} procesado")
        return {"status": "warm", "asin": result.asin}
    except Exception as e:
        logger.warning(f"⚠️ [WARMUP] Fallo: {e}")
        return {"status": "partial", "error": str(e)}


# =============================================================================
# ENDPOINT RESILIENTE — Segunda pasada (retry agresivo + estados de negocio)
# =============================================================================
# Diseñado para re-procesar ASINs que quedaron como "Error" en el Sheet.
# Se invoca desde n8n después de que el flujo principal termine.
#
# Diferencias vs /fetch-and-evaluate:
#   1. Usa AGGRESSIVE_POLICY (5 intentos, backoff 5s → 10s → 20s → 40s ±30%)
#   2. NO propaga 403/404 como error → los convierte en estados de negocio
#   3. Nunca devuelve 500 al cliente (siempre N8nListingOutput válido)
#   4. Solo 400/401 siguen siendo errores duros (config/auth roto)
# =============================================================================
@router.get("/fetch-and-evaluate-resilient/{marketplace}/{asin}", response_model=N8nListingOutput)
async def fetch_and_evaluate_resilient(marketplace: str, asin: str):
    print(f"\n>>> [RESILIENT] PETICIÓN -> ASIN: {asin}, Marketplace: {marketplace} <<<\n", flush=True)
    """
    Versión resiliente para segunda pasada.

    - Retry agresivo (5 intentos con backoff exponencial + jitter).
    - 403 → "Marketplace no autorizado" (estado de negocio)
    - 404 → "Descontinuado / No Encontrado" (estado de negocio)
    - Otros fallos → "Error tras reintentos"
    - Nunca devuelve 5xx: siempre N8nListingOutput válido.
    """
    logger.info(
        f"🛡️ [RESILIENT] Iniciando segunda pasada -> ASIN={asin} | Marketplace={marketplace}"
    )

    try:
        input_data = await sp_api_client.fetch_catalog_item(
            asin=asin,
            marketplace=marketplace,
            policy=AGGRESSIVE_POLICY,
        )
        print(f">>> [RESILIENT] DATA EXTRAÍDA: {input_data.model_dump()}\n", flush=True)

        logger.info(f"📦 [RESILIENT SP-API OK] Datos obtenidos para {asin}:")
        logger.info(f"  ├─ Título: {len(input_data.title or '')} chars")
        logger.info(f"  ├─ Bullet Points: {len(input_data.bullet_points or [])} ítems")
        logger.info(f"  ├─ Imágenes: {len(input_data.image_urls or [])} URLs")
        logger.info(f"  └─ Variaciones: {len(input_data.variation_asins or [])} ASINs")

        result = evaluate_listing(input_data)

        if hasattr(result, "to_n8n_output"):
            final_output = result.to_n8n_output(asin=asin)
        else:
            final_output = result

        logger.info(
            f"✅ [RESILIENT OK] ASIN={asin} ({marketplace}) | "
            f"Status={final_output.listing_status if hasattr(final_output, 'listing_status') else 'OK'}"
        )
        return final_output

    except HTTPException as e:
        # 🔑 400/401 → error duro, no reintentable, se propaga
        if e.status_code in (400, 401):
            logger.error(
                f"❌ [RESILIENT CONFIG] ASIN={asin} ({marketplace}) | "
                f"HTTP {e.status_code}: {e.detail}"
            )
            raise e

        # 🔑 403 → Marketplace no autorizado (el seller no vende ahí)
        if e.status_code == 403:
            return _build_business_state_fallback(
                asin=asin,
                marketplace=marketplace,
                status="Marketplace no autorizado",
                detail=f"El seller no tiene acceso a este marketplace. Detalle: {e.detail}",
            )

        # 🔑 404 → ASIN no existe en este marketplace (descontinuado o inválido)
        if e.status_code == 404:
            return _build_business_state_fallback(
                asin=asin,
                marketplace=marketplace,
                status="Descontinuado / No Encontrado",
                detail=f"ASIN no encontrado en Amazon ({marketplace}). Detalle: {e.detail}",
            )

        # 🔑 5xx u otros → error tras reintentos (ya se agotó AGGRESSIVE_POLICY)
        return _build_business_state_fallback(
            asin=asin,
            marketplace=marketplace,
            status="Error tras reintentos",
            detail=f"HTTP {e.status_code}: {e.detail}",
        )

    except Exception as e:
        logger.error(
            f"💥 [RESILIENT UNHANDLED] ASIN={asin} ({marketplace}): {e}",
            exc_info=True,
        )
        return _build_business_state_fallback(
            asin=asin,
            marketplace=marketplace,
            status="Error tras reintentos",
            detail=str(e),
        )