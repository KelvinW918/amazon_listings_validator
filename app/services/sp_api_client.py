import asyncio
import html
import logging
import random
import re
import time
from dataclasses import dataclass
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
from curl_cffi.requests import AsyncSession
from app.core.config import settings
from app.models.listing import ListingValidationInput

logger = logging.getLogger("sp_api_client")

# Pool de User-Agents reales para rotación anti-CAPTCHA
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Pool de impersonaciones de curl_cffi (rotación de fingerprint TLS)
IMPERSONATE_POOL = ["chrome120", "chrome124", "chrome131"]


# =============================================================================
# POLÍTICA DE REINTENTOS CON EXPONENTIAL BACKOFF + JITTER
# =============================================================================
# Objetivo: absorber 429 (Too Many Requests), 5xx y timeouts de Amazon sin
# propagar el fallo al flujo. Se definen 2 presets para SP-API y 2 para el
# scraper HTML (que históricamente usaba delays distintos):
#   SP-API:
#     - DEFAULT_POLICY         → 3 intentos, 1s, 2s (comportamiento histórico)
#     - AGGRESSIVE_POLICY      → 5 intentos, 5s → 10s → 20s → 40s ±30%
#   Scraper:
#     - SCRAPE_DEFAULT_POLICY  → 3 intentos, 3s, 6s, 10s (comportamiento histórico)
#     - SCRAPE_AGGRESSIVE_POLICY → 5 intentos, 5s → 10s → 20s → 40s ±30%
# =============================================================================

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0        # delay del primer retry (segundos)
    multiplier: float = 2.0         # factor exponencial
    max_delay: float = 6.0          # tope duro por intento
    jitter_ratio: float = 0.0       # ±ratio sobre el delay calculado (0 = sin jitter)


def compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """
    Calcula el delay para el intento N con exponential backoff + jitter opcional.

    Ejemplos:
      SP-API DEFAULT_POLICY (attempt=1) → 1.0s
      SP-API AGGRESSIVE_POLICY (attempt=3) → 20.0s ±30% → [14, 26]
      SCRAPE_DEFAULT_POLICY (attempt=1) → 3.0s
      SCRAPE_DEFAULT_POLICY (attempt=3) → 12.0s → cap 10.0s
    """
    raw = policy.base_delay * (policy.multiplier ** (attempt - 1))
    raw = min(raw, policy.max_delay)
    if policy.jitter_ratio > 0:
        jitter = raw * policy.jitter_ratio * (2 * random.random() - 1)
        raw = raw + jitter
    return max(0.5, raw)


# --- Políticas SP-API ---
DEFAULT_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=1.0,
    multiplier=2.0,
    max_delay=6.0,
    jitter_ratio=0.0,
)

AGGRESSIVE_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=5.0,
    multiplier=2.0,
    max_delay=60.0,
    jitter_ratio=0.3,
)

# --- Políticas del scraper HTML ---
# SCRAPE_DEFAULT replica la lógica histórica: 3s, 6s, 10s con jitter interno.
SCRAPE_DEFAULT_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=3.0,
    multiplier=2.0,
    max_delay=10.0,
    jitter_ratio=0.0,
)

SCRAPE_AGGRESSIVE_POLICY = RetryPolicy(
    max_attempts=5,
    base_delay=5.0,
    multiplier=2.0,
    max_delay=60.0,
    jitter_ratio=0.3,
)


# =============================================================================
# LIMPIEZA DE BULLET POINTS
# =============================================================================
# SP-API devuelve los bullets con HTML crudo incrustado, por ejemplo:
#   '<span style="font-family:Verdana;font-size:14px;">🚙 TEXTO:</span> descripción...'
# Si no limpiamos ese HTML:
#   1. Los tags cuentan como caracteres → falsos positivos de "excede 250 chars".
#   2. El regex de formato 'Emoji + MAYÚSCULAS + :' falla porque el string
#      empieza por '<' en lugar de por el emoji.
# =============================================================================
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')


def _clean_bullet(raw_value: str) -> str:
    """
    Limpia un bullet proveniente de SP-API:
      1. Elimina TODAS las etiquetas HTML (<span>, <b>, <br>, etc.).
      2. Decodifica entidades HTML (&amp; → &, &#x1F699; → 🚙).
      3. Normaliza espacios en blanco (\\n, \\t, espacios múltiples → 1 espacio).

    Preserva emojis y caracteres Unicode nativos intactos.
    """
    if not raw_value:
        return ""
    # 1. Quitar etiquetas HTML
    text = _HTML_TAG_RE.sub('', raw_value)
    # 2. Decodificar entidades HTML
    text = html.unescape(text)
    # 3. Normalizar espacios en blanco
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return text


# =============================================================================
# UMBRAL DE HTML COMPLETO (para detección fiable de A+)
# =============================================================================
# Amazon sirve versiones recortadas del HTML a bots/curl_cffi en algunos casos:
#   - HTML completo: 1.5M - 2.5M chars → A+ detectable si existe
#   - HTML recortado: ~800K chars → A+ omitido del final del documento
# Si el HTML es más corto que este umbral, NO podemos afirmar que no tiene A+.
# =============================================================================
_HTML_COMPLETE_THRESHOLD = 1_200_000


class SPAPIException(Exception): pass
class SPAPIRateLimitException(SPAPIException): pass
class SPAPIServerException(SPAPIException): pass

class SPAPIClient:
    """Cliente multi-marketplace con integración nativa a Catalog API v2022-04-01, A+ Content API v2020-11-01 y Product Pricing API v0."""

    def __init__(self):
        limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
        self.client = httpx.AsyncClient(timeout=15.0, limits=limits)

        self._token_cache: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {
            "NA": asyncio.Lock(),
            "EU": asyncio.Lock(),
            "FE": asyncio.Lock(),
        }

        # 🔑 Sesión persistente para el scraping (mantiene cookies entre requests)
        self._scrape_session: Optional[AsyncSession] = None
        self._scrape_session_lock = asyncio.Lock()

    async def close(self):
        """Cierra todas las sesiones activas."""
        if self._scrape_session is not None:
            try:
                await self._scrape_session.close()
            except Exception:
                pass
            self._scrape_session = None
        await self.client.aclose()

    async def _get_scrape_session(self) -> AsyncSession:
        """
        Devuelve la sesión persistente de scraping.
        La primera vez la crea; en llamadas posteriores reutiliza la misma
        para mantener el cookie jar de Amazon.
        """
        async with self._scrape_session_lock:
            if self._scrape_session is None:
                self._scrape_session = AsyncSession()
                logger.info("🍪 [SCRAPE SESSION] Nueva sesión persistente creada (cookie jar activo)")
            return self._scrape_session

    async def warmup_scrape_session(self) -> bool:
        """
        Solución 3: Pre-calentamiento real de la sesión de scraping.
        Hace una petición real a Amazon con un ASIN conocido para:
          - Cargar curl_cffi en memoria
          - Establecer cookies de sesión con Amazon
          - Reducir CAPTCHA en las primeras peticiones reales

        Retorna True si el warmup tuvo éxito.
        """
        try:
            logger.info("🔥 [SCRAPE WARMUP] Iniciando pre-calentamiento de sesión con Amazon...")

            session = await self._get_scrape_session()

            # Usamos un ASIN estable y conocido (ajusta si quieres usar otro)
            warmup_asin = "B0F298DTW1"
            warmup_url = f"https://www.amazon.es/dp/{warmup_asin}"

            res = await session.get(
                warmup_url,
                impersonate="chrome124",
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Upgrade-Insecure-Requests": "1",
                },
                timeout=15.0,
                allow_redirects=True,
            )

            if res.status_code == 200:
                has_captcha = (
                    "api-services-support@amazon.com" in res.text
                    or "validateCaptcha" in res.text
                    or "Robot Check" in res.text
                )
                if has_captcha:
                    logger.warning("⚠️ [SCRAPE WARMUP] Amazon devolvió CAPTCHA en warmup (IP bloqueada)")
                    return False

                # Verificar que obtuvimos HTML real (contiene marcadores conocidos)
                has_real_html = (
                    "imageBlock" in res.text
                    or "landingImage" in res.text
                    or "productTitle" in res.text
                )
                if has_real_html:
                    logger.info(
                        f"✅ [SCRAPE WARMUP] Sesión caliente OK | "
                        f"{len(res.text)} chars | cookies establecidas"
                    )
                    return True
                else:
                    logger.warning("⚠️ [SCRAPE WARMUP] HTML sospechoso (sin marcadores esperados)")
                    return False
            else:
                logger.warning(f"⚠️ [SCRAPE WARMUP] Status inesperado: {res.status_code}")
                return False

        except Exception as e:
            logger.warning(f"⚠️ [SCRAPE WARMUP] Fallo: {e}")
            return False

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

    # =========================================================================
    # HTTP GET CON RETRY CONFIGURABLE (reemplaza el decorador tenacity)
    # =========================================================================
    async def _execute_http_get(
        self,
        endpoint: str,
        headers: Dict[str, str],
        params: Dict[str, Any],
        policy: RetryPolicy = DEFAULT_POLICY,
        context: str = "",
    ) -> httpx.Response:
        """
        GET con retry manual + jitter para 429/5xx/timeouts.

        NO reintenta en 401, 403, 404 → esos son estados de negocio
        y se devuelven como respuesta para que el caller decida.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                res = await self.client.get(endpoint, headers=headers, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt >= policy.max_attempts:
                    logger.error(
                        f"❌ [RETRY EXHAUSTED NET] {context} | {type(e).__name__} | "
                        f"intento {attempt}/{policy.max_attempts}"
                    )
                    raise
                wait_s = compute_backoff(attempt, policy)
                logger.warning(
                    f"⏸️ [RETRY NET] {context} | {type(e).__name__} | "
                    f"intento {attempt}/{policy.max_attempts} | esperando {wait_s:.1f}s"
                )
                await asyncio.sleep(wait_s)
                continue

            # 429 → retry con backoff
            if res.status_code == 429:
                last_exc = SPAPIRateLimitException(
                    f"Rate limit 429 alcanzado en Amazon SP-API. Contexto: {context}"
                )
                if attempt >= policy.max_attempts:
                    logger.error(
                        f"❌ [RETRY EXHAUSTED 429] {context} | "
                        f"intento {attempt}/{policy.max_attempts}"
                    )
                    raise last_exc
                wait_s = compute_backoff(attempt, policy)
                logger.warning(
                    f"⏸️ [RETRY 429] {context} | "
                    f"intento {attempt}/{policy.max_attempts} | esperando {wait_s:.1f}s"
                )
                await asyncio.sleep(wait_s)
                continue

            # 5xx → retry con backoff
            if 500 <= res.status_code < 600:
                last_exc = SPAPIServerException(
                    f"Error interno del servidor de Amazon SP-API: {res.status_code}. Contexto: {context}"
                )
                if attempt >= policy.max_attempts:
                    logger.error(
                        f"❌ [RETRY EXHAUSTED 5xx] {context} | status {res.status_code} | "
                        f"intento {attempt}/{policy.max_attempts}"
                    )
                    raise last_exc
                wait_s = compute_backoff(attempt, policy)
                logger.warning(
                    f"⏸️ [RETRY 5xx] {context} | status {res.status_code} | "
                    f"intento {attempt}/{policy.max_attempts} | esperando {wait_s:.1f}s"
                )
                await asyncio.sleep(wait_s)
                continue

            # Cualquier otro status (200, 400, 401, 403, 404) → devolver sin retry
            return res

        # Seguridad: nunca debería llegar aquí
        if last_exc:
            raise last_exc
        raise SPAPIException(f"Retry loop agotado sin excepción registrada. Contexto: {context}")

    async def _fetch_raw_catalog_item(
        self,
        asin: str,
        marketplace_id: str,
        region: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> Dict[str, Any]:
        token = await self.get_lwa_access_token(region)
        base_url = settings.REGION_ENDPOINTS[region]
        endpoint = f"{base_url}/catalog/2022-04-01/items/{asin}"
        headers = {"x-amz-access-token": token, "Content-Type": "application/json"}
        params = {
            "marketplaceIds": marketplace_id,
            "includedData": "attributes,images,summaries,relationships"
        }

        context = f"SP-API Catalog asin={asin} market={marketplace_id}"
        res = await self._execute_http_get(endpoint, headers, params, policy=policy, context=context)

        if res.status_code in (401, 403):
            self._token_cache.pop(region, None)
            new_token = await self.get_lwa_access_token(region)
            headers["x-amz-access-token"] = new_token
            res = await self._execute_http_get(endpoint, headers, params, policy=policy, context=context)

        if res.status_code != 200:
            raise HTTPException(
                status_code=res.status_code if res.status_code in [400, 401, 403, 404] else 502,
                detail=f"Error desde Amazon SP-API Catalog ({res.status_code}): {res.text}"
            )

        return res.json()

    # =========================================================================
    # BUYABILITY — Product Pricing API (fuente oficial de estado)
    # =========================================================================
    async def _fetch_buyability_from_pricing_api(
        self,
        asin: str,
        marketplace_id: str,
        region: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> Optional[bool]:
        """
        Detecta si el listing es comprable usando Product Pricing API v0.

        Retorna:
            True  → ≥1 oferta activa → Listing activo
            False → 0 ofertas → Listing inactivo/no comprable
            None  → Error en la consulta → N/A
        """
        try:
            token = await self.get_lwa_access_token(region)
            base_url = settings.REGION_ENDPOINTS[region]
            headers = {"x-amz-access-token": token, "Content-Type": "application/json"}

            context = f"SP-API Pricing asin={asin} market={marketplace_id}"
            res = await self._execute_http_get(
                f"{base_url}/products/pricing/v0/items/{asin}/offers",
                headers,
                {"MarketplaceId": marketplace_id, "ItemCondition": "New"},
                policy=policy,
                context=context,
            )

            if res.status_code != 200:
                logger.warning(
                    f"⚠️ [PRICING] ASIN {asin} ({region}) → status {res.status_code}"
                )
                return None

            data = res.json()
            payload = data.get("payload", {})
            summary = payload.get("Summary", {})
            total_offers = summary.get("TotalOfferCount", 0)

            logger.info(
                f"💰 [PRICING] ASIN {asin} | Total ofertas: {total_offers} "
                f"→ {'COMPRABLE' if total_offers > 0 else 'NO COMPRABLE'}"
            )

            return total_offers > 0

        except Exception as e:
            logger.warning(f"⚠️ [PRICING] Error consultando {asin}: {e}")
            return None

    async def _resolve_buyability(
        self,
        asin: str,
        raw_data: Dict[str, Any],
        marketplace_id: str,
        region: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> Optional[bool]:
        """
        Resuelve el estado de buyability del ASIN consultado, manejando
        correctamente el caso de Parent ASINs.

        Lógica:
        - Si el ASIN es PADRE (tiene hijos directos) → los padres no tienen
          ofertas propias en Amazon. Se consulta el estado del PRIMER HIJO
          como proxy representativo del estado del grupo familiar.
        - Si el ASIN es HIJO o STANDALONE → consultar directamente.

        Retorna:
            True / False / None (según Product Pricing API)
        """
        # Detectar si es PADRE consultando hijos directos en el payload
        direct_children = self._extract_child_asins(raw_data)

        if direct_children:
            # Es PADRE → usar el primer hijo como proxy
            proxy_asin = sorted(set(direct_children))[0]
            logger.info(
                f"👨 ASIN {asin} es PADRE con {len(direct_children)} hijos. "
                f"Consultando buyability del hijo proxy: {proxy_asin}"
            )
            return await self._fetch_buyability_from_pricing_api(
                proxy_asin, marketplace_id, region, policy=policy
            )

        # Es HIJO o STANDALONE → consultar directo
        return await self._fetch_buyability_from_pricing_api(
            asin, marketplace_id, region, policy=policy
        )

    async def _fetch_aplus_content(
        self,
        asin: str,
        marketplace_id: str,
        region: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> Dict[str, Any]:
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

            context_docs = f"SP-API A+ docs asin={asin} market={marketplace_id}"
            res_docs = await self._execute_http_get(
                endpoint_docs, headers, params_docs, policy=policy, context=context_docs
            )

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
                        context_detail = f"SP-API A+ detail doc={doc_id}"
                        res_detail = await self._execute_http_get(
                            endpoint_detail, headers, params_detail, policy=policy, context=context_detail
                        )

                        if res_detail.status_code == 200:
                            detail_data = res_detail.json()
                            doc_content = detail_data.get("contentDocument", {}).get("contentRecord", {}).get("contentData", {})
                            modules = doc_content.get("modules", [])

                            for mod in modules:
                                mod_type = mod.get("moduleType")

                                if mod_type in ["STANDARD_VIDEO", "PREMIUM_VIDEO"]:
                                    result["has_video_in_aplus"] = True

                                if mod_type in ["STANDARD_COMPARISON_TABLE", "PREMIUM_COMPARISON_TABLE"]:
                                    comp_table = mod.get("standardComparisonTable") or mod.get("premiumComparisonTable", {})
                                    product_cols = comp_table.get("productColumns", [])
                                    result["a_plus_comparison_products_count"] = len(product_cols)

                                    if len(product_cols) > 0:
                                        result["a_plus_has_price"] = True
                                        result["a_plus_has_reviews"] = True

        except Exception as e:
            logger.warning(f"No se pudo consultar A+ Content API para ASIN {asin}: {e}")

        return result

    # =========================================================================
    # PARSER DE VIDEO — Aisla la galería principal para evitar falsos positivos
    # =========================================================================
    def _parse_video_from_html(self, html: str) -> bool:
        """
        Detecta si el producto tiene video en la galería.
        Aísla las listas de medios del producto (altImages + desktop-media-mainView)
        para evitar falsos positivos de videos en recomendaciones.
        """
        if not html:
            return False

        html_low = html.lower()

        safe_zones = []

        alt_match = re.search(
            r'<div[^>]*id=["\']altimages["\'][^>]*>.*?<ul[^>]*>(.*?)</ul>',
            html_low, re.DOTALL | re.IGNORECASE
        )
        if alt_match:
            safe_zones.append(alt_match.group(1))

        main_match = re.search(
            r'<ul[^>]*class=["\'][^"\']*desktop-media-mainview[^"\']*["\'][^>]*>(.*?)</ul>',
            html_low, re.DOTALL | re.IGNORECASE
        )
        if main_match:
            safe_zones.append(main_match.group(1))

        if safe_zones:
            safe_area = " ".join(safe_zones)
            safe_markers = [
                'data-csa-c-media-type="video"',
                "videoblock",
                "vse-player",
                "videoingress",
                "openvideoimmersiveview",
                'id="videocount"',
                '"type":"chromeful-video"',
            ]
            found = any(m in safe_area for m in safe_markers)
            logger.info(
                f"{'✓' if found else '✗'} [VIDEO] Zonas seguras: "
                f"{len(safe_zones)} listas, {len(safe_area)} chars → {found}"
            )
            return found

        ultra_specific = [
            'data-csa-c-media-type="video"',
            'id="videocount"',
            "openvideoimmersiveview",
        ]
        found = any(m in html_low for m in ultra_specific)
        logger.info(f"{'✓' if found else '✗'} [VIDEO] Fallback sin listas aisladas → {found}")
        return found

    # =========================================================================
    # PARSER DE CONTEO DE IMÁGENES DESDE HTML
    # =========================================================================
    def _parse_image_count_from_html(self, html: str, current_asin: str) -> Optional[int]:
        """
        Extrae el conteo de imágenes del carrusel desde el HTML público.
        Sirve como override cuando SP-API devuelve menos imágenes que la página real.

        Cuenta los <li class="imageThumbnail"> en altImages excluyendo el de video.
        """
        if not html:
            return None

        html_low = html.lower()

        # Buscar el bloque altImages (carrusel de thumbnails)
        alt_match = re.search(
            r'<div[^>]*id=["\']altimages["\'][^>]*>(.*?)(?=<div[^>]*id=["\'](?:main-image-container|desktop-media)["\']|$)',
            html_low,
            re.DOTALL | re.IGNORECASE
        )
        if not alt_match:
            return None

        alt_block = alt_match.group(1)

        # Contar los <li> con clase "imageThumbnail" (imágenes del carrusel)
        image_thumbnails = re.findall(
            r'<li[^>]*class=["\'][^"\']*imagethumbnail[^"\']*["\'][^>]*>',
            alt_block,
            re.IGNORECASE
        )

        # Excluir los que tienen "videoThumbnail" en sus clases
        image_count = sum(
            1 for li in image_thumbnails
            if "videothumbnail" not in li.lower()
        )

        if image_count > 0:
            logger.info(f"📸 [HTML IMAGES] ASIN {current_asin} | contadas en HTML: {image_count}")
            return image_count

        return None

    # =========================================================================
    # PARSER DE A+ — Ampliado para amazon.es + diagnóstico de marcadores
    # =========================================================================
    def _parse_aplus_from_html(self, html: str, current_asin: str) -> Dict[str, Any]:
        """Parser de A+ ampliado con diagnóstico profundo."""
        result = {
            "has_a_plus": False,
            "a_plus_comparison_products_count": 0,
            "a_plus_has_price": True,
            "a_plus_has_reviews": True,
        }

        if not html:
            return result

        html_low = html.lower()

        aplus_markers = [
            'id="aplus"', "id='aplus'",
            'id="aplus_feature_div"',
            'id="aplus3p_feature_div"',
            'id="aplusbrandstory_feature_div"',
            "aplus-v2", "aplus-v3",
            "aplus-module", "aplusbrandstory",
            "aplus_feature_div",
            "aplus-desktop",
        ]
        if any(marker in html_low for marker in aplus_markers):
            result["has_a_plus"] = True

        if not result["has_a_plus"]:
            logger.info(f"🔎 [A+ DIAGNOSTIC] ASIN: {current_asin} | Sin marcadores A+ → NO tiene A+")
            return result

        aplus_block = ""
        block_patterns = [
            r'<div[^>]*id=["\']aplus_feature_div["\'][^>]*>(.*?)(?=<div[^>]*id=["\'](?:productdetails|reviewsheader|sp_detail|customer_reviews|detailbullets)["\']|$)',
            r'<div[^>]*id=["\']aplus["\'][^>]*>(.*?)(?=<div[^>]*id=["\'](?:productdetails|reviewsheader|sp_detail)["\']|$)',
            r'<div[^>]*id=["\']aplus3p_feature_div["\'][^>]*>(.*?)(?=<div[^>]*id=["\'](?:productdetails|reviewsheader|sp_detail)["\']|$)',
            r'<div[^>]*id=["\']aplusbrandstory_feature_div["\'][^>]*>(.*?)(?=<div[^>]*id=["\'](?:productdetails|reviewsheader|sp_detail)["\']|$)',
        ]
        for pattern in block_patterns:
            m = re.search(pattern, html_low, re.DOTALL | re.IGNORECASE)
            if m:
                aplus_block = m.group(1)
                break

        search_area = aplus_block if aplus_block else html_low

        table_classes = re.findall(r'<table[^>]*class=["\']([^"\']*)["\']', search_area)
        all_compar = set(re.findall(r'[a-z\-_]*compar[a-z\-_]*', search_area))
        all_table_classes = set(re.findall(r'class=["\']([^"\']*table[^"\']*)["\']', search_area))
        all_aplus_classes = set(re.findall(r'class=["\']([^"\']*aplus[^"\']*)["\']', search_area))
        asins_in_block = set(re.findall(r'/dp/([A-Z0-9]{10})', search_area)) - {current_asin}

        logger.info(
            f"🔬 [A+ DEEP] ASIN: {current_asin} | "
            f"Bloque A+: {len(aplus_block)} chars | "
            f"Tables: {len(table_classes)} | "
            f"'compar' hits: {list(all_compar)[:10]} | "
            f"ASINs en bloque A+: {len(asins_in_block)} → {list(asins_in_block)[:8]}"
        )

        comparison_markers = [
            "standard-comparison-table",
            "premium-comparison-table",
            "apm-tablemodule",
            "aplus-comparison-table",
            "premium-module-6",
            "comparison_table",
            "aplus-tech-spec-table",
        ]
        if any(marker in search_area for marker in comparison_markers):
            table_block = ""
            table_patterns = [
                r'<table[^>]*class=["\'][^"\']*apm-tablemodule[^"\']*["\'][^>]*>.*?</table>',
                r'<table[^>]*class=["\'][^"\']*(?:standard|premium)-comparison-table[^"\']*["\'][^>]*>.*?</table>',
                r'<table[^>]*class=["\'][^"\']*aplus-comparison-table[^"\']*["\'][^>]*>.*?</table>',
                r'<div[^>]*class=["\'][^"\']*premium-module-6[^"\']*["\'][^>]*>.*?(?=<div[^>]*class=["\'][^"\']*aplus-module|$)',
            ]
            for pattern in table_patterns:
                m = re.search(pattern, search_area, re.DOTALL | re.IGNORECASE)
                if m:
                    table_block = m.group(0)
                    break

            count_area = table_block if table_block else search_area

            asins_dp = set(re.findall(r'/dp/([A-Z0-9]{10})', count_area))
            asins_gp = set(re.findall(r'/gp/product/([A-Z0-9]{10})', count_area))
            asins_all = (asins_dp | asins_gp) - {current_asin}

            if asins_all:
                result["a_plus_comparison_products_count"] = min(len(asins_all), 6)
            else:
                img_cells = len(re.findall(r'apm-tablemodule-image', count_area))
                header_cells = len(re.findall(r'apm-tablemodule-keyheader', count_area))
                fallback = max(img_cells, header_cells, 4)
                result["a_plus_comparison_products_count"] = min(fallback, 6)

            logger.info(
                f"✓ [A+ ESTRATEGIA 1] Tabla aislada: {len(table_block)} chars | "
                f"ASINs únicos: {len(asins_all)} → count final: {result['a_plus_comparison_products_count']}"
            )
            return result

        has_table_tag = "<table" in search_area
        if asins_in_block and (has_table_tag or len(asins_in_block) >= 3):
            count = min(len(asins_in_block), 6)
            if 4 <= count <= 6:
                result["a_plus_comparison_products_count"] = count
                logger.info(f"✓ [A+ ESTRATEGIA 2] {count} ASINs detectados en bloque A+")
                return result
            elif count < 4 and has_table_tag:
                result["a_plus_comparison_products_count"] = count
                logger.info(f"✓ [A+ ESTRATEGIA 2-ALT] Tabla + {count} ASINs (<4)")
                return result

        if any("compar" in c.lower() for c in table_classes):
            fallback_count = max(len(asins_in_block), 4)
            result["a_plus_comparison_products_count"] = min(fallback_count, 6)
            logger.info(f"✓ [A+ ESTRATEGIA 3] Tabla con 'compar' → count: {result['a_plus_comparison_products_count']}")
            return result

        logger.warning(
            f"⚠️ [A+ SIN TABLA] ASIN {current_asin} tiene A+ pero no se detectó tabla comparativa."
        )
        return result

    # =========================================================================
    # PARSER DE RESEÑAS
    # =========================================================================
    def _parse_review_count_from_html(self, html: str) -> Optional[int]:
        """Extrae el conteo de reseñas del HTML público de Amazon (4 estrategias)."""
        if not html:
            return None

        match = re.search(r'data-hook=["\']total-review-count["\'][^>]*>([^<]+)<', html)
        if match:
            clean = re.sub(r'[^\d]', '', match.group(1))
            if clean.isdigit():
                return int(clean)

        match = re.search(r'acrCustomerReviewText[^\d]*([\d\.,]+)', html)
        if match:
            clean = re.sub(r'[^\d]', '', match.group(1))
            if clean.isdigit():
                return int(clean)

        match = re.search(r'data-hook=["\']total-review-count-number["\'][^>]*>([^<]+)<', html)
        if match:
            clean = re.sub(r'[^\d]', '', match.group(1))
            if clean.isdigit():
                return int(clean)

        match = re.search(
            r'([\d\.,]+)\s*(?:ratings?|reseñas?|reviews?|valoraciones?|calificaciones?)',
            html,
            re.IGNORECASE
        )
        if match:
            clean = re.sub(r'[^\d]', '', match.group(1))
            if clean.isdigit():
                return int(clean)

        return None

    # =========================================================================
    # FETCH DEL HTML PÚBLICO — Sesión persistente + curl_cffi
    # =========================================================================
    async def _fetch_public_html(
        self,
        asin: str,
        marketplace: str,
        retry_delay: float = 0.0
    ) -> Optional[str]:
        """
        Descarga el HTML público con curl_cffi usando la sesión persistente.
        La sesión compartida mantiene las cookies de Amazon entre requests,
        reduciendo la probabilidad de CAPTCHA.
        """
        if retry_delay > 0:
            logger.info(f"⏳ Esperando {retry_delay}s antes de scrapear {asin} (anti-CAPTCHA)")
            await asyncio.sleep(retry_delay)

        domain_map = {
            "ES": "es", "DE": "de", "FR": "fr", "IT": "it",
            "UK": "co.uk", "NL": "nl", "BE": "com.be", "IE": "ie",
            "SE": "se", "PL": "pl",
            "TR": "com.tr", "AE": "ae", "SA": "sa",
            "US": "com", "CA": "ca", "MX": "com.mx",
            "JP": "co.jp", "AU": "com.au",
        }

        mp_upper = marketplace.upper()
        domain = domain_map.get(mp_upper)

        if not domain:
            logger.warning(
                f"⚠️ [SCRAPE] Marketplace '{marketplace}' no está en domain_map. "
                f"Cayendo a amazon.com como fallback seguro."
            )
            domain = "com"

        url = f"https://www.amazon.{domain}/dp/{asin}"

        accept_lang_map = {
            "ES": "es-ES,es;q=0.9,en;q=0.8",
            "DE": "de-DE,de;q=0.9,en;q=0.8",
            "FR": "fr-FR,fr;q=0.9,en;q=0.8",
            "IT": "it-IT,it;q=0.9,en;q=0.8",
            "UK": "en-GB,en;q=0.9",
            "NL": "nl-NL,nl;q=0.9,en;q=0.8",
            "BE": "nl-BE,nl;q=0.9,fr-BE;q=0.8,en;q=0.7",
            "IE": "en-IE,en;q=0.9",
            "SE": "sv-SE,sv;q=0.9,en;q=0.8",
            "PL": "pl-PL,pl;q=0.9,en;q=0.8",
            "TR": "tr-TR,tr;q=0.9,en;q=0.8",
            "AE": "ar-AE,ar;q=0.9,en;q=0.8",
            "SA": "ar-SA,ar;q=0.9,en;q=0.8",
            "US": "en-US,en;q=0.9",
            "CA": "en-CA,en;q=0.9,fr-CA;q=0.8",
            "MX": "es-MX,es;q=0.9,en;q=0.8",
            "JP": "ja-JP,ja;q=0.9,en;q=0.8",
            "AU": "en-AU,en;q=0.9",
        }
        accept_lang = accept_lang_map.get(mp_upper, "en-US,en;q=0.9")

        # 🔑 Rotación en CADA intento (no solo en el primero)
        chosen_impersonate = random.choice(IMPERSONATE_POOL)
        ua = random.choice(USER_AGENTS)

        # Pequeño jitter aleatorio antes del request (evita patrones detectables)
        jitter = random.uniform(0.3, 1.5)
        await asyncio.sleep(jitter)

        logger.info(
            f"🌐 [SCRAPE REQUEST] ASIN: {asin} | Marketplace: {mp_upper} | "
            f"URL: {url} | impersonate: {chosen_impersonate} | jitter: {jitter:.2f}s"
        )

        try:
            # 🔑 Usar la sesión persistente (comparte cookies entre requests)
            session = await self._get_scrape_session()

            res = await session.get(
                url,
                impersonate=chosen_impersonate,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": accept_lang,
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
                timeout=15.0,
                allow_redirects=True,
            )

            if res.status_code != 200:
                logger.warning(f"Scrape ASIN {asin} devolvió status {res.status_code}")
                return None

            html = res.text

            # 🔑 Validación de tamaño mínimo
            # Amazon devuelve páginas de error/CAPTCHA con HTTP 200 pero
            # solo ~300-350K chars. Las páginas reales pesan ~1.5-2.5M chars.
            # Si el HTML es muy pequeño, es una página de bloqueo/error disfrazada.
            if len(html) < 500_000:
                logger.warning(
                    f"⚠️ [SCRAPE TOO SMALL] ASIN {asin} | "
                    f"solo {len(html)} chars (esperado >500K) → posible error/CAPTCHA"
                )
                return None

            if (
                "api-services-support@amazon.com" in html
                or "validateCaptcha" in html
                or "Type the characters you see" in html
                or "Robot Check" in html
            ):
                logger.warning(f"⚠️ [SCRAPE BLOCKED] CAPTCHA para ASIN {asin} ({marketplace})")
                return None

            logger.info(
                f"✅ [SCRAPE OK] ASIN {asin} | {len(html)} chars | "
                f"impersonate: {chosen_impersonate} | domain: amazon.{domain}"
            )
            return html

        except Exception as e:
            logger.warning(f"Fallo de red al scrapear ASIN {asin}: {e}")
            return None

    async def _fetch_public_html_with_retry(
        self,
        asin: str,
        marketplace: str,
        max_attempts: int = 3,
        policy: RetryPolicy = SCRAPE_DEFAULT_POLICY,
    ) -> Optional[str]:
        """
        Intenta scrapear el ASIN con reintentos y delays crecientes según la policy.

        Con SCRAPE_DEFAULT_POLICY (histórico): 3s → 6s → 10s
        Con SCRAPE_AGGRESSIVE_POLICY: 5s → 10s → 20s → 40s ±30%

        El parámetro `max_attempts` permite override puntual; si es menor que
        policy.max_attempts, se usa el máximo entre ambos.
        """
        effective_attempts = max(max_attempts, policy.max_attempts)

        for attempt in range(1, effective_attempts + 1):
            if attempt == 1:
                delay = 0.0
            else:
                # compute_backoff(attempt - 1, policy): para attempt=2 → 1ra iteración del backoff
                delay = compute_backoff(attempt - 1, policy)

            html = await self._fetch_public_html(asin, marketplace, retry_delay=delay)
            if html:
                if attempt > 1:
                    logger.info(
                        f"✅ [SCRAPE RETRY OK] {asin} recuperado en intento "
                        f"{attempt}/{effective_attempts}"
                    )
                return html

            if attempt < effective_attempts:
                next_delay = compute_backoff(attempt, policy)
                logger.info(
                    f"↻ [SCRAPE RETRY] {asin} ({marketplace}) | "
                    f"intento {attempt + 1}/{effective_attempts} en {next_delay:.0f}s"
                )

        logger.warning(f"❌ Scrape de {asin} falló tras {effective_attempts} intentos")
        return None

    async def _fallback_public_page_check(
        self,
        asin: str,
        marketplace: str,
        policy: RetryPolicy = SCRAPE_DEFAULT_POLICY,
    ) -> Dict[str, Any]:
        """
        Extrae Video, A+, Reseñas y A+ Comparativa desde la vista pública de Amazon.
        Incluye banderas `scrape_success` y `html_complete` para que el motor
        pueda distinguir entre "no tiene", "no se pudo verificar" y "HTML recortado".
        """
        result = {
            "has_video": False,
            "has_a_plus": False,
            "a_plus_comparison_products_count": 0,
            "a_plus_has_price": True,
            "a_plus_has_reviews": True,
            "review_count": None,
            "scrape_success": False,
            "image_count_from_html": None,
            "html_complete": False,
        }

        html = await self._fetch_public_html_with_retry(
            asin, marketplace, max_attempts=policy.max_attempts, policy=policy
        )
        if not html:
            return result

        result["scrape_success"] = True
        # 🔑 NUEVO: determinar si el HTML es completo o recortado
        result["html_complete"] = len(html) >= _HTML_COMPLETE_THRESHOLD

        result["has_video"] = self._parse_video_from_html(html)

        # 🔑 NUEVO: contar imágenes del HTML
        result["image_count_from_html"] = self._parse_image_count_from_html(html, asin)

        aplus_parsed = self._parse_aplus_from_html(html, asin)
        a_plus_detected = aplus_parsed.get("has_a_plus", False)

        if a_plus_detected:
            result["has_a_plus"] = True
            result["a_plus_comparison_products_count"] = aplus_parsed.get("a_plus_comparison_products_count", 0)
            result["a_plus_has_price"] = aplus_parsed.get("a_plus_has_price", True)
            result["a_plus_has_reviews"] = aplus_parsed.get("a_plus_has_reviews", True)
        elif not result["html_complete"]:
            # 🔑 HTML recortado: NO podemos afirmar que no tiene A+ → indeterminado (None)
            result["has_a_plus"] = None
            logger.warning(
                f"⚠️ [A+ INDETERMINADO] ASIN {asin} | "
                f"HTML recortado ({len(html)} chars < {_HTML_COMPLETE_THRESHOLD}) "
                f"→ no se puede confirmar ausencia de A+"
            )
        else:
            # HTML completo + sin marcadores A+ → confirmado que NO tiene
            result["has_a_plus"] = False

        result["review_count"] = self._parse_review_count_from_html(html)

        logger.info(
            f"🔎 [SCRAPE RESULT] ASIN: {asin} | Video: {result['has_video']} | "
            f"A+: {result['has_a_plus']} (comparativa: {result['a_plus_comparison_products_count']}) | "
            f"Reviews: {result['review_count']} | "
            f"Images HTML: {result['image_count_from_html']} | "
            f"HTML complete: {result['html_complete']} ({len(html)} chars)"
        )

        return result

    # =========================================================================
    # RESOLUCIÓN DE FAMILIA DE VARIACIONES
    # =========================================================================
    def _extract_parent_asin(self, raw_data: Dict[str, Any]) -> Optional[str]:
        """Extrae el Parent ASIN desde relationships[]."""
        relationships_by_marketplace = raw_data.get("relationships", []) or []

        for mkt_rel in relationships_by_marketplace:
            rels = mkt_rel.get("relationships", []) or []
            for rel in rels:
                rel_type = rel.get("type")
                if rel_type in ["VARIATION", "VARIATION_PARENT"]:
                    parent_asins = rel.get("parentAsins", [])
                    if parent_asins:
                        return parent_asins[0]
                if "parentAsins" in rel and rel["parentAsins"]:
                    return rel["parentAsins"][0]

        return None

    def _extract_child_asins(self, raw_data: Dict[str, Any]) -> List[str]:
        """Extrae todos los childAsins del payload."""
        children: List[str] = []
        for mkt_rel in raw_data.get("relationships", []) or []:
            for rel in mkt_rel.get("relationships", []) or []:
                child_asins = rel.get("childAsins", [])
                if child_asins:
                    children.extend(child_asins)
        return list(set(children))

    async def _resolve_variation_family(
        self,
        asin: str,
        raw_data: Dict[str, Any],
        parent_asin: Optional[str],
        marketplace_id: str,
        region: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> List[str]:
        """
        Construye la lista de variantes REALES (child ASINs) de la familia.

        IMPORTANTE: NO incluimos el Parent ASIN porque es un contenedor virtual
        que agrupa hijos, no una variante real del producto.

        Casos:
        1. ASIN es PADRE → devolvemos sus hijos (children)
        2. ASIN es HIJO  → devolvemos sus hermanos (children del padre, incluyendo el propio ASIN)
        3. ASIN standalone → devolvemos [asin] (1 elemento → N/A en la validación)

        Esto evita falsos OK en listings con "single child family"
        (padre con 1 solo hijo, que no son variaciones reales).
        """
        # Caso 1: El ASIN actual es PADRE → sus hijos son las variantes
        direct_children = self._extract_child_asins(raw_data)
        if direct_children:
            unique_children = sorted(set(direct_children))
            logger.info(
                f"ASIN {asin} es PADRE. Variantes reales (hijos): {len(unique_children)} "
                f"→ {unique_children[:5]}{'...' if len(unique_children) > 5 else ''}"
            )
            return unique_children

        # Caso 2: El ASIN actual es HIJO → sus hermanos son las variantes
        if parent_asin:
            try:
                parent_data = await self._fetch_raw_catalog_item(
                    parent_asin, marketplace_id, region, policy=policy
                )
                siblings = self._extract_child_asins(parent_data)
                if siblings:
                    unique_siblings = sorted(set(siblings))
                    logger.info(
                        f"ASIN {asin} es HIJO de {parent_asin}. Variantes reales (hermanos): {len(unique_siblings)} "
                        f"→ {unique_siblings[:5]}{'...' if len(unique_siblings) > 5 else ''}"
                    )
                    return unique_siblings
                else:
                    logger.info(
                        f"ASIN {asin} es HIJO de {parent_asin}, pero el padre no expone childAsins. "
                        f"Tratando como standalone."
                    )
                    return [asin]
            except Exception as e:
                logger.warning(
                    f"No se pudo consultar el padre {parent_asin}: {e}. Tratando como standalone."
                )
                return [asin]

        # Caso 3: ASIN standalone sin parent ni children
        logger.info(f"ASIN {asin} es standalone (sin variaciones)")
        return [asin]

    # =========================================================================
    # FLUJO PRINCIPAL
    # =========================================================================
    async def fetch_catalog_item(
        self,
        asin: str,
        marketplace: str,
        policy: RetryPolicy = DEFAULT_POLICY,
    ) -> ListingValidationInput:
        mkt_code = self._normalize(marketplace)
        marketplace_id = settings.MARKETPLACE_IDS.get(mkt_code)
        if not marketplace_id:
            raise HTTPException(status_code=400, detail=f"Marketplace no soportado: {marketplace}")

        region = self._get_region(mkt_code)

        # 🔑 Traducir la política SP-API a la política del scraper.
        # DEFAULT_POLICY (SP-API) → SCRAPE_DEFAULT_POLICY (scraper)
        # AGGRESSIVE_POLICY (SP-API) → SCRAPE_AGGRESSIVE_POLICY (scraper)
        scraper_policy = (
            SCRAPE_AGGRESSIVE_POLICY if policy is AGGRESSIVE_POLICY
            else SCRAPE_DEFAULT_POLICY
        )

        if getattr(settings, "ENABLE_MOCK", False):
            logger.info(f"ENABLE_MOCK activo. Generando datos simulados para ASIN {asin} en {mkt_code}.")
            return self._get_mock_fallback_data(asin, mkt_code)

        try:
            raw_data = await self._fetch_raw_catalog_item(
                asin, marketplace_id, region, policy=policy
            )

            parent_asin = self._extract_parent_asin(raw_data)
            if parent_asin:
                logger.info(f"ASIN {asin} detectado como Hijo. Parent ASIN: {parent_asin}")
            else:
                logger.info(f"ASIN {asin} no tiene Parent ASIN (standalone o padre directo).")

            # 🔑 Consultar buyability con manejo correcto de Parent ASIN
            pricing_buyable = await self._resolve_buyability(
                asin=asin,
                raw_data=raw_data,
                marketplace_id=marketplace_id,
                region=region,
                policy=policy,
            )

            variation_family = await self._resolve_variation_family(
                asin=asin,
                raw_data=raw_data,
                parent_asin=parent_asin,
                marketplace_id=marketplace_id,
                region=region,
                policy=policy,
            )

            aplus_data = await self._fetch_aplus_content(
                asin, marketplace_id, region, policy=policy
            )

            if not aplus_data["has_a_plus"] and parent_asin:
                logger.info(f"ASIN {asin} sin A+ directo. Reintentando A+ con Parent ASIN: {parent_asin}")
                aplus_data = await self._fetch_aplus_content(
                    parent_asin, marketplace_id, region, policy=policy
                )

            # 🔑 Scraper con la política traducida
            fallback_data = await self._fallback_public_page_check(
                asin, mkt_code, policy=scraper_policy
            )

            html_complete = fallback_data.get("html_complete", False)

            if not fallback_data["has_a_plus"] and parent_asin and parent_asin != asin:
                logger.info(f"ASIN {asin} sin A+ en HTML. Scrapeando padre {parent_asin} con retry...")
                parent_html = await self._fetch_public_html_with_retry(
                    parent_asin, mkt_code, max_attempts=3, policy=scraper_policy
                )
                if parent_html:
                    parent_aplus = self._parse_aplus_from_html(parent_html, parent_asin)
                    if parent_aplus.get("has_a_plus"):
                        logger.info(
                            f"✅ A+ heredado desde padre {parent_asin}: "
                            f"{parent_aplus['a_plus_comparison_products_count']} productos en comparativa"
                        )
                        fallback_data["has_a_plus"] = True
                        fallback_data["a_plus_comparison_products_count"] = parent_aplus.get("a_plus_comparison_products_count", 0)
                        fallback_data["a_plus_has_price"] = parent_aplus.get("a_plus_has_price", True)
                        fallback_data["a_plus_has_reviews"] = parent_aplus.get("a_plus_has_reviews", True)
                    else:
                        logger.warning(f"Padre {parent_asin} scrapeado OK pero sin marcadores A+")

            if fallback_data["has_a_plus"]:
                aplus_data["has_a_plus"] = True
                if not aplus_data.get("a_plus_comparison_products_count"):
                    aplus_data["a_plus_comparison_products_count"] = fallback_data["a_plus_comparison_products_count"]
                aplus_data["a_plus_has_price"] = True
                aplus_data["a_plus_has_reviews"] = True

            if fallback_data["has_video"]:
                aplus_data["has_video_in_aplus"] = True

            review_count_child = fallback_data.get("review_count")
            has_video_fallback = fallback_data.get("has_video", False)
            scrape_success = fallback_data.get("scrape_success", False)
            image_count_from_html = fallback_data.get("image_count_from_html")

            review_count_parent = None
            if parent_asin and parent_asin != asin and review_count_child is not None:
                try:
                    parent_html_for_reviews = await self._fetch_public_html_with_retry(
                        parent_asin, mkt_code, max_attempts=2, policy=scraper_policy
                    )
                    if parent_html_for_reviews:
                        review_count_parent = self._parse_review_count_from_html(parent_html_for_reviews)
                        logger.info(
                            f"Reviews → Hijo {asin}: {review_count_child} | "
                            f"Padre {parent_asin}: {review_count_parent}"
                        )
                except Exception as e:
                    logger.warning(f"No se pudo obtener reviews del padre {parent_asin}: {e}")

            return await self._parse_sp_api_to_input(
                asin=asin,
                marketplace=mkt_code,
                data=raw_data,
                aplus_data=aplus_data,
                marketplace_id=marketplace_id,
                region=region,
                variation_family=variation_family,
                review_count_child=review_count_child,
                review_count_parent=review_count_parent,
                has_video_fallback=has_video_fallback,
                scrape_success=scrape_success,
                is_buyable=pricing_buyable,
                image_count_from_html=image_count_from_html,
                html_complete=html_complete,
            )

        except (SPAPIRateLimitException, SPAPIServerException) as e:
            logger.error(f"Error de infraestructura SP-API para ASIN {asin}: {e}")
            raise HTTPException(status_code=503, detail=f"Servicio de Amazon SP-API no disponible: {str(e)}")
        except httpx.HTTPError as e:
            logger.error(f"Error HTTP de transporte hacia SP-API: {e}")
            raise HTTPException(status_code=502, detail=f"Fallo de transporte con Amazon SP-API: {str(e)}")

    async def _parse_sp_api_to_input(
        self,
        asin: str,
        marketplace: str,
        data: Dict[str, Any],
        aplus_data: Dict[str, Any],
        marketplace_id: str,
        region: str,
        variation_family: List[str],
        review_count_child: Optional[int] = None,
        review_count_parent: Optional[int] = None,
        has_video_fallback: bool = False,
        scrape_success: bool = False,
        is_buyable: Optional[bool] = None,
        image_count_from_html: Optional[int] = None,
        html_complete: bool = False,
    ) -> ListingValidationInput:
        attributes = data.get("attributes", {})
        summaries_list = data.get("summaries", [])
        summaries = summaries_list[0] if summaries_list and isinstance(summaries_list, list) else {}

        title = summaries.get("itemName", "") if isinstance(summaries, dict) else ""
        if not title and "item_name" in attributes:
            title = attributes["item_name"][0].get("value", "")

        # 🔑 FIX BULLETS: limpiar HTML crudo incrustado por SP-API.
        bullets_raw = attributes.get("bullet_point", [])
        bullet_points = (
            [_clean_bullet(b.get("value", "")) for b in bullets_raw]
            if isinstance(bullets_raw, list) else []
        )
        # Filtrar bullets que quedaron vacíos tras la limpieza
        bullet_points = [b for b in bullet_points if b]

        # =====================================================================
        # 3. Imágenes — Agrupar por `variant` para contar imágenes reales
        # =====================================================================
        def _url_base(url: str) -> str:
            """Extrae el ID base de la URL de Amazon (sin sufijo de tamaño)."""
            return re.sub(
                r'\._[A-Z0-9_,]+_\.(jpg|jpeg|png|webp|gif)$',
                '',
                url,
                flags=re.IGNORECASE
            )

        variants_map: Dict[str, Dict[str, Any]] = {}       # Imágenes CON variant
        unique_no_variant: Dict[str, Dict[str, Any]] = {}  # Imágenes SIN variant (dedupe por URL base)
        images_list = data.get("images", [])
        total_entries = 0

        if images_list and isinstance(images_list, list):
            for img_container in images_list:
                for img_data in img_container.get("images", []):
                    total_entries += 1
                    variant = img_data.get("variant")
                    link = img_data.get("link") or img_data.get("Link")
                    if not link:
                        continue

                    height = img_data.get("height") or 0
                    width = img_data.get("width") or 0
                    size = max(int(height) if height else 0, int(width) if width else 0)

                    if variant:
                        # Tiene variant asignado (MAIN, PT01...)
                        current = variants_map.get(variant)
                        if current is None or size > current["size"]:
                            variants_map[variant] = {"url": link, "size": size}
                    else:
                        # Sin variant → dedupe por URL base
                        base = _url_base(link)
                        current = unique_no_variant.get(base)
                        if current is None or size > current["size"]:
                            unique_no_variant[base] = {"url": link, "size": size}

        # Combinar: primero los variants conocidos (ordenados), luego los sin-variant
        image_urls: List[str] = []
        for v in sorted(variants_map.keys()):
            image_urls.append(variants_map[v]["url"])
        for base in sorted(unique_no_variant.keys()):
            image_urls.append(unique_no_variant[base]["url"])

        logger.info(
            f"📸 [IMAGES] ASIN {asin} | "
            f"entries totales: {total_entries} | "
            f"variants con nombre: {len(variants_map)} | "
            f"sin variant (únicos): {len(unique_no_variant)} | "
            f"TOTAL SP-API: {len(image_urls)}"
        )

        # 🔑 OVERRIDE: Si el HTML tiene MÁS imágenes que SP-API, usar el conteo del HTML
        if image_count_from_html and image_count_from_html > len(image_urls):
            logger.info(
                f"📸 [IMAGE OVERRIDE] ASIN {asin} | "
                f"SP-API: {len(image_urls)} → HTML: {image_count_from_html} → usando {image_count_from_html}"
            )
            while len(image_urls) < image_count_from_html:
                image_urls.append(f"https://html-override.placeholder/{asin}_{len(image_urls)}.jpg")

        variation_asins = variation_family if variation_family else [asin]

        has_video_explicit = (
            has_video_fallback
            or aplus_data.get("has_video_in_aplus", False)
            or "video_display_group" in attributes
        )

        if has_video_explicit:
            has_video: Optional[bool] = True
        elif scrape_success:
            has_video = False
        else:
            has_video = None

        variant_review_counts: Optional[List[int]] = None
        if review_count_child is not None and review_count_parent is not None:
            if review_count_child == review_count_parent:
                variant_review_counts = [review_count_child] * len(variation_asins)
            else:
                variant_review_counts = [review_count_child, review_count_parent]
        elif review_count_child is not None:
            variant_review_counts = [review_count_child] * len(variation_asins)
        elif review_count_parent is not None:
            variant_review_counts = [review_count_parent] * len(variation_asins)

        # =====================================================================
        # 🔑 DETERMINACIÓN DE has_a_plus (con soporte para HTML recortado)
        # =====================================================================
        has_a_plus_sp = aplus_data.get("has_a_plus", False)
        a_plus_count = aplus_data.get("a_plus_comparison_products_count", 0)

        if has_a_plus_sp:
            has_a_plus: Optional[bool] = True
        elif not scrape_success:
            has_a_plus = None  # no se pudo verificar
        elif not html_complete:
            has_a_plus = None  # HTML recortado: indeterminado
        else:
            has_a_plus = False  # HTML completo + no detectado → confirmado sin A+

        # 🔑 Determinar el estado real del listing
        if is_buyable is not None:
            is_active_value: Optional[bool] = is_buyable
        else:
            is_active_value = None

        return ListingValidationInput(
            asin=asin,
            marketplace=marketplace,
            is_active=is_active_value,
            title=title,
            bullet_points=bullet_points,
            variation_asins=variation_asins,
            image_urls=image_urls,
            has_video=has_video,
            has_a_plus=has_a_plus,
            a_plus_comparison_products_count=a_plus_count,
            a_plus_has_price=aplus_data.get("a_plus_has_price", False),
            a_plus_has_reviews=aplus_data.get("a_plus_has_reviews", False),
            variant_review_counts=variant_review_counts
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