import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from fastapi import HTTPException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
from app.core.config import settings
from app.models.listing import ListingValidationInput

logger = logging.getLogger("sp_api_client")

class SPAPIException(Exception): pass
class SPAPIRateLimitException(SPAPIException): pass
class SPAPIServerException(SPAPIException): pass

class SPAPIClient:
    """Cliente multi-marketplace con integración nativa a Catalog API v2022-04-01 y A+ Content API v2020-11-01."""

    def __init__(self):
        limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
        self.client = httpx.AsyncClient(timeout=15.0, limits=limits)

        self._token_cache: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {
            "NA": asyncio.Lock(),
            "EU": asyncio.Lock(),
            "FE": asyncio.Lock(),
        }

    async def close(self):
        await self.client.aclose()

    def _normalize(self, marketplace: str) -> str:
        code = marketplace.strip().upper()
        aliases = getattr(settings, "MARKETPLACE_ALIASES", {})
        return aliases.get(code, code)

    def _get_region(self, marketplace: str) -> str:
        code = self._normalize(marketplace)
        if code in ["US", "CA", "MX", "BR"]:
            return "NA"
        if code in ["JP", "AU", "SG"]:
            return "FE"
        return "EU"

    def _get_refresh_token_for_region(self, region: str) -> str:
        token_attr = f"SP_API_REFRESH_TOKEN_{region}"
        token = getattr(settings, token_attr, None) or getattr(settings, "SP_API_REFRESH_TOKEN", None)
        if not token:
            raise HTTPException(
                status_code=400,
                detail=f"No se encontró SP_API_REFRESH_TOKEN configurado para la región {region}."
            )
        return token

    async def get_lwa_access_token(self, region: str) -> str:
        now = time.time()
        cached = self._token_cache.get(region)

        if cached and now < (cached["expires_at"] - 60):
            return cached["token"]

        lock = self._locks.get(region, asyncio.Lock())
        async with lock:
            cached = self._token_cache.get(region)
            if cached and now < (cached["expires_at"] - 60):
                return cached["token"]

            refresh_token = self._get_refresh_token_for_region(region)
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.SP_API_CLIENT_ID,
                "client_secret": settings.SP_API_CLIENT_SECRET,
            }

            try:
                response = await self.client.post(settings.LWA_TOKEN_URL, data=payload)
            except Exception as e:
                logger.error(f"Error de red al autenticar LWA ({region}): {e}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Fallo de conexión con servidor LWA ({region}): {str(e)}"
                )

            if response.status_code != 200:
                logger.error(f"Falla crítica LWA ({region}) [{response.status_code}]: {response.text}")
                raise HTTPException(
                    status_code=401,
                    detail=f"Error de autenticación LWA ({region}) [{response.status_code}]: {response.text}"
                )

            token_data = response.json()
            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)

            if not access_token:
                raise HTTPException(
                    status_code=401,
                    detail=f"Respuesta LWA de región {region} no contiene access_token válido."
                )

            self._token_cache[region] = {
                "token": access_token,
                "expires_at": time.time() + expires_in
            }
            logger.info(f"Nuevo token LWA generado exitosamente para región {region}.")
            return access_token

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException, SPAPIRateLimitException, SPAPIServerException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def _execute_http_get(self, endpoint: str, headers: Dict[str, str], params: Dict[str, Any]) -> httpx.Response:
        res = await self.client.get(endpoint, headers=headers, params=params)
        if res.status_code == 429:
            raise SPAPIRateLimitException("Rate limit 429 alcanzado en Amazon SP-API.")
        if 500 <= res.status_code < 600:
            raise SPAPIServerException(f"Error interno del servidor de Amazon SP-API: {res.status_code}.")
        return res

    async def _fetch_raw_catalog_item(self, asin: str, marketplace_id: str, region: str) -> Dict[str, Any]:
        token = await self.get_lwa_access_token(region)
        base_url = settings.REGION_ENDPOINTS[region]
        endpoint = f"{base_url}/catalog/2022-04-01/items/{asin}"
        headers = {"x-amz-access-token": token, "Content-Type": "application/json"}
        params = {
            "marketplaceIds": marketplace_id,
            "includedData": "attributes,images,summaries,relationships"
        }

        res = await self._execute_http_get(endpoint, headers, params)

        if res.status_code in (401, 403):
            self._token_cache.pop(region, None)
            new_token = await self.get_lwa_access_token(region)
            headers["x-amz-access-token"] = new_token
            res = await self._execute_http_get(endpoint, headers, params)

        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code if res.status_code in [400, 401, 403, 404] else 502,
                detail=f"Error desde Amazon SP-API Catalog ({res.status_code}): {res.text}"
            )

        return res.json()

    async def _fetch_aplus_content(self, asin: str, marketplace_id: str, region: str) -> Dict[str, Any]:
        """Consulta la A+ Content API para validar presencia de módulos, tablas comparativas y video."""
        result = {
            "has_a_plus": False,
            "a_plus_comparison_products_count": 0,
            "a_plus_has_price": False,
            "a_plus_has_reviews": False,
            "has_video_in_aplus": False
        }

        try:
            token = await self.get_lwa_access_token(region)
            base_url = settings.REGION_ENDPOINTS[region]
            headers = {"x-amz-access-token": token, "Content-Type": "application/json"}

            endpoint_docs = f"{base_url}/aplus/2020-11-01/asins/{asin}/documents"
            params_docs = {"marketplaceId": marketplace_id}

            res_docs = await self._execute_http_get(endpoint_docs, headers, params_docs)

            if res_docs.status_code == 200:
                docs_data = res_docs.json()
                records = docs_data.get("contentRecords", [])

                if records:
                    result["has_a_plus"] = True
                    doc_id = records[0].get("contentDocumentId")

                    if doc_id:
                        endpoint_detail = f"{base_url}/aplus/2020-11-01/contentDocuments/{doc_id}"
                        params_detail = {
                            "marketplaceId": marketplace_id,
                            "includedDataSet": "CONTENTS"
                        }
                        res_detail = await self._execute_http_get(endpoint_detail, headers, params_detail)

                        if res_detail.status_code == 200:
                            detail_data = res_detail.json()
                            doc_content = detail_data.get("contentDocument", {}).get("contentRecord", {}).get("contentData", {})
                            modules = doc_content.get("modules", [])

                            for mod in modules:
                                mod_type = mod.get("moduleType")

                                # Detección de video dentro del A+
                                if mod_type in ["STANDARD_VIDEO", "PREMIUM_VIDEO"]:
                                    result["has_video_in_aplus"] = True

                                # Detección de Tabla Comparativa
                                if mod_type in ["STANDARD_COMPARISON_TABLE", "PREMIUM_COMPARISON_TABLE"]:
                                    comp_table = mod.get("standardComparisonTable") or mod.get("premiumComparisonTable", {})
                                    product_cols = comp_table.get("productColumns", [])
                                    result["a_plus_comparison_products_count"] = len(product_cols)

                                    # Si existen columnas de productos en la tabla, Amazon activa
                                    # automáticamente el bloque de precio y reseñas en el frontend.
                                    if len(product_cols) > 0:
                                        result["a_plus_has_price"] = True
                                        result["a_plus_has_reviews"] = True

        except Exception as e:
            logger.warning(f"No se pudo consultar A+ Content API para ASIN {asin}: {e}")

        return result

    def _parse_video_from_html(self, html: str) -> bool:
        """Determina si hay un video en el HTML de la página del producto."""
        import re

        # Aislamos el bloque de imágenes/galería principal para evitar falsos positivos
        img_block_match = re.search(
            r'id=["\'](?:imageBlock|altImages|main-image-container)["\'].*?(?=<div id=["\'](?:dp-container|description|aplus)["\']|\Z)',
            html,
            re.DOTALL | re.IGNORECASE
        )
        gallery_html = img_block_match.group(0) if img_block_match else html

        # Clases CSS y marcadores habituales de reproductores de video en Amazon
        video_markers = ["vse-video-container", "videoBlock", "video-slate-container", "vse-player", "a-video-player"]

        if any(marker in gallery_html for marker in video_markers):
            return True

        return "media-amazon.com/images/S/vse-vcta-media-page" in html or "videos/m/" in html

    async def _fallback_public_page_check(self, asin: str, marketplace: str) -> Dict[str, Any]:
        """Extrae Video y A+ directamente de la vista pública de Amazon aislando el contenedor A+."""
        domain_map = {"MX": "com.mx", "US": "com", "ES": "es", "BR": "com.br", "CA": "ca"}
        domain = domain_map.get(marketplace.upper(), "com.mx")
        url = f"https://www.amazon.{domain}/dp/{asin}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        }

        result = {
            "has_video": False,
            "has_a_plus": False,
            "a_plus_comparison_products_count": 0,
            "a_plus_has_price": True,
            "a_plus_has_reviews": True,
        }

        try:
            res = await self.client.get(url, headers=headers, follow_redirects=True, timeout=10.0)
            if res.status_code == 200:
                html = res.text

                # 1. Detección de Video usando la función helper
                result["has_video"] = self._parse_video_from_html(html)

                # 2. Detección de A+ Content
                if "aplus-v2" in html or "aplus3p" in html or "aplus-module" in html:
                    result["has_a_plus"] = True

                # 3. Detección precisa de Tabla Comparativa dentro del bloque A+
                import re

                # Aislamos el HTML correspondiente a la sección A+ para evitar falsos positivos de recomendaciones
                aplus_match = re.search(r'id=["\']aplus["\'].*?(?=<div id=["\'](?:productDetails|detailBullets|reviewsHeader)["\']|\Z)', html, re.DOTALL | re.IGNORECASE)
                aplus_html = aplus_match.group(0) if aplus_match else html

                # Clases CSS estándar de tablas comparativas A+ en Amazon (Módulo 6 y Premium)
                if any(tag in aplus_html for tag in ["apm-tablemodule", "aplus-comparison-table", "premium-module-6"]):
                    # Extraer los enlaces ASIN (/dp/ASIN) dentro de la tabla A+
                    table_match = re.search(r'<table[^>]*class=["\'][^handler]*?apm-tablemodule.*?</table>', aplus_html, re.DOTALL)
                    table_html = table_match.group(0) if table_match else aplus_html

                    # Estrategia 1: ASINs en formato /dp/ o /gp/product/
                    asins_dp = set(re.findall(r'/dp/([A-Z0-9]{10})', table_html))
                    asins_gp = set(re.findall(r'/gp/product/([A-Z0-9]{10})', table_html))
                    asins_all = asins_dp | asins_gp

                    # Estrategia 2: Contar celdas de producto reales (excluye headers y filas de atributos)
                    # En el módulo 6, cada producto tiene una celda con imagen + una con el título/botón
                    # El keyheader aparece una vez por fila (atributo), así que lo excluimos del conteo de productos
                    product_image_cells = len(re.findall(r'apm-tablemodule-image', table_html))

                    # Estrategia 3: Contar columnas por los <td> de la fila de imágenes
                    # Estructura típica: <tr> con N <td> de imágenes
                    image_row_match = re.search(r'<tr[^>]*>(?:\s*<td[^>]*>\s*<img[^>]*apm-tablemodule-image[^>]*>.*?</tr>', table_html, re.DOTALL)
                    if image_row_match:
                        image_row_html = image_row_match.group(0)
                        cols_in_row = len(re.findall(r'<td', image_row_html))
                        if cols_in_row > 0:
                            product_image_cells = cols_in_row

                    if asins_all:
                        result["a_plus_comparison_products_count"] = len(asins_all)
                    elif product_image_cells > 0:
                        result["a_plus_comparison_products_count"] = product_image_cells
                    else:
                        # Fallback seguro: si la estructura existe pero no pudimos contar, asumimos 6
                        result["a_plus_comparison_products_count"] = 6

        except Exception as e:
            logger.warning(f"Fallback HTTP check falló para ASIN {asin}: {e}")

        return result

    async def fetch_catalog_item(self, asin: str, marketplace: str) -> ListingValidationInput:
        mkt_code = self._normalize(marketplace)
        marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
        if not marketplace_id:
            raise HTTPException(status_code=400, detail=f"Marketplace no soportado: {marketplace}")

        region = self._get_region(mkt_code)

        if getattr(settings, "ENABLE_MOCK", False):
            logger.info(f"ENABLE_MOCK activo. Generando datos simulados para ASIN {asin} en {mkt_code}.")
            return self._get_mock_fallback_data(asin, mkt_code)

        try:
            # 1. Obtener datos del catálogo
            raw_data = await self._fetch_raw_catalog_item(asin, marketplace_id, region)

            # Extract parent ASIN si existe
            parent_asin = None
            for mkt_rel in raw_data.get("relationships", []):
                for rel in mkt_rel.get("relationships", []):
                    if "parentAsins" in rel and rel["parentAsins"]:
                        parent_asin = rel["parentAsins"][0]
                        break

            # 2. Consultar A+ primero para el ASIN Hijo, si da vacío, probar con el ASIN Padre
            aplus_data = await self._fetch_aplus_content(asin, marketplace_id, region)
            if not aplus_data["has_a_plus"] and parent_asin:
                aplus_data = await self._fetch_aplus_content(parent_asin, marketplace_id, region)

            # 3. Si A+ dio error 403 o no se detectó video por API, se ejecuta el fallback público
            if not aplus_data["has_a_plus"] or not aplus_data.get("has_video_in_aplus"):
                fallback_data = await self._fallback_public_page_check(asin, mkt_code)

                if fallback_data["has_a_plus"]:
                    aplus_data["has_a_plus"] = True
                    aplus_data["a_plus_comparison_products_count"] = fallback_data["a_plus_comparison_products_count"]
                    aplus_data["a_plus_has_price"] = True
                    aplus_data["a_plus_has_reviews"] = True

                if fallback_data["has_video"]:
                    aplus_data["has_video_in_aplus"] = True

            return await self._parse_sp_api_to_input(asin, mkt_code, raw_data, aplus_data, marketplace_id, region)

        except (SPAPIRateLimitException, SPAPIServerException) as e:
            logger.error(f"Error de infraestructura SP-API para ASIN {asin}: {e}")
            raise HTTPException(status_code=503, detail=f"Servicio de Amazon SP-API no disponible: {str(e)}")
        except httpx.HTTPError as e:
            logger.error(f"Error HTTP de transporte hacia SP-API: {e}")
            raise HTTPException(status_code=502, detail=f"Fallo de transporte con Amazon SP-API: {str(e)}")

    async def _parse_sp_api_to_input(
        self, asin: str, marketplace: str, data: Dict[str, Any], aplus_data: Dict[str, Any], marketplace_id: str, region: str
    ) -> ListingValidationInput:
        attributes = data.get("attributes", {})
        summaries_list = data.get("summaries", [])
        summaries = summaries_list[0] if summaries_list and isinstance(summaries_list, list) else {}
        relationships_raw = data.get("relationships", [])

        # 1. Título
        title = summaries.get("itemName", "") if isinstance(summaries, dict) else ""
        if not title and "item_name" in attributes:
            title = attributes["item_name"][0].get("value", "")

        # 2. Bullet Points
        bullets_raw = attributes.get("bullet_point", [])
        bullet_points = [b.get("value", "") for b in bullets_raw] if isinstance(bullets_raw, list) else []

        # 3. Imágenes
        image_urls = []
        images_list = data.get("images", [])
        if images_list and isinstance(images_list, list):
            seen_variants = set()
            for img_container in images_list:
                for img_data in img_container.get("images", []):
                    variant = img_data.get("variant")
                    link = img_data.get("link") or img_data.get("Link")
                    if link and (variant not in seen_variants or not variant):
                        image_urls.append(link)
                        if variant:
                            seen_variants.add(variant)

        # 4. Variaciones (Relación Padre / Hijos)
        extracted_children = []
        parent_asin = None

        for mkt_rel in relationships_raw:
            for rel in mkt_rel.get("relationships", []):
                if "childAsins" in rel and rel["childAsins"]:
                    extracted_children.extend(rel["childAsins"])
                if "parentAsins" in rel and rel["parentAsins"]:
                    parent_asin = rel["parentAsins"][0]

        if not extracted_children and parent_asin:
            try:
                parent_data = await self._fetch_raw_catalog_item(parent_asin, marketplace_id, region)
                for mkt_rel in parent_data.get("relationships", []):
                    for rel in mkt_rel.get("relationships", []):
                        if "childAsins" in rel and rel["childAsins"]:
                            extracted_children.extend(rel["childAsins"])
            except Exception as e:
                logger.warning(f"No se pudieron consultar variaciones del padre {parent_asin}: {e}")

        variation_asins = list(set(extracted_children)) or [asin]

        # Detección de Video: Revisa si hay módulo de video en A+ o atributos multimedia en el catálogo
        has_video = aplus_data.get("has_video_in_aplus", False) or "video_display_group" in attributes

        return ListingValidationInput(
            asin=asin,
            marketplace=marketplace,
            is_active=True,
            title=title,
            bullet_points=bullet_points,
            variation_asins=variation_asins,
            image_urls=image_urls,
            has_video=has_video,
            has_a_plus=aplus_data.get("has_a_plus", False),
            a_plus_comparison_products_count=aplus_data.get("a_plus_comparison_products_count", 0),
            a_plus_has_price=aplus_data.get("a_plus_has_price", False),
            a_plus_has_reviews=aplus_data.get("a_plus_has_reviews", False),
            variant_review_counts=None
        )

    def _get_mock_fallback_data(self, asin: str, marketplace: str) -> ListingValidationInput:
        return ListingValidationInput(
            asin=asin,
            marketplace=marketplace.upper(),
            is_active=True,
            title="[DEMO] Producto Optimizado - Cumple SOP Domy",
            bullet_points=[
                "Bullet 1: Alta calidad garantizada para auditoría",
                "Bullet 2: Material resistente y duradero",
                "Bullet 3: Diseño ergonómico avanzado"
            ],
            variation_asins=[asin],
            image_urls=["https://via.placeholder.com/500", "https://via.placeholder.com/500"],
            has_video=True,
            has_a_plus=True,
            a_plus_comparison_products_count=3,
            a_plus_has_price=True,
            a_plus_has_reviews=True,
            variant_review_counts=[45, 120]
        )

sp_api_client = SPAPIClient()