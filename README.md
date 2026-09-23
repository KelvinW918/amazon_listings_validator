# 🛒 Amazon Listings Validator (SOP Domy Engine)

Sistema backend de alta disponibilidad y motor de auditoría automatizada para la validación de calidad de listings en Amazon, conforme a la normativa SOP de Domy Company.

Combina una **API REST asíncrona construida en FastAPI**, extracción híbrida multirregión con **Amazon Selling Partner API (SP-API)** y un flujo de orquestación mensual en **n8n** que procesa en lote **16 marketplaces simultáneos**, generando reportes consolidados en **Google Sheets**.

---

## 📋 Tabla de Contenidos

1. [Visión General y Arquitectura](#-visión-general-y-arquitectura)
2. [Reglas de Validación SOP (8 Pilares)](#-reglas-de-validación-sop-8-pilares)
3. [Estructura del Proyecto](#-estructura-del-proyecto)
4. [Componentes Principales](#️-componentes-principales)
   - [Motor de Reglas (Validator Service)](#1-motor-de-reglas-appservicesvalidatorpy)
   - [Cliente SP-API Híbrido (SP-API Client)](#2-cliente-sp-api-híbrido-appservicessp_api_clientpy)
   - [Esquemas y Modelos Pydantic](#3-esquemas-y-modelos-pydantic)
   - [Capa API REST](#4-capa-api-rest-appapiv1validatorpy)
5. [Flujo de Automatización en n8n](#-flujo-de-automatización-en-n8n)
6. [Variables de Entorno y Configuración](#-variables-de-entorno-y-configuración)
7. [Guía de Instalación y Despliegue](#-guía-de-instalación-y-despliegue)
8. [Documentación de Endpoints](#-documentación-de-endpoints)

---

## 🔍 Visión General y Arquitectura

El sistema garantiza que todos los ASINs del catálogo de Domy Company cumplan al **100%** con los estándares de conversión, optimización SEO y experiencia de usuario.

```
                                    ┌────────────────────────────────────────────────────────┐
                                    │                     n8n Workflow                       │
                                    │  (Schedule Trigger: Día 5 / Mes - 16 Marketplaces)     │
                                    └───────────────────────────┬────────────────────────────┘
                                                                │
                                                                ▼
                                                ┌───────────────────────────────┐
                                                │   GET /fetch-and-evaluate     │
                                                │   POST /batch-fetch-evaluate  │
                                                └───────────────┬───────────────┘
                                                                │
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                             FastAPI Backend Engine                                              │
│                                                                                                                 │
│   ┌───────────────────────────────────┐    ┌───────────────────────────────────┐    ┌───────────────────────┐   │
│   │        SP-API Client              │    │          SOP Validator            │    │     Fallback Engine   │   │
│   │  - LWA Multi-Region Token Cache   │───>│  - 8 Reglas SOP Deterministas     │───>│ - Estructura N8n 1:1  │   │
│   │  - Catalog API v2022-04-01        │    │  - Regex Emojis + NFC Normaliz.   │    │ - Log de fallos red   │   │
│   │  - A+ Content API v2020-11-01     │    │  - Unificación de Reseñas         │    │                       │   │
│   │  - Public DOM Scraping Fallback   │    │                                   │    │                       │   │
│   └───────────────────────────────────┘    └───────────────────────────────────┘    └───────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                                │
                                                                ▼
                                                ┌───────────────────────────────┐
                                                │     Google Sheets Output      │
                                                │ (16 Pestañas / Lotes de ~400) │
                                                └───────────────────────────────┘
```

---

## 📐 Reglas de Validación SOP (8 Pilares)

El motor evalúa **determinísticamente** cada ASIN devolviendo diagnósticos estrictos (`OK`, `NOK`, `N/A`) y estados de comprabilidad (`Activo` / `Inactivo`).

| # | Pilar SOP | Función | Criterio de Cumplimiento (OK) | Criterio de Fallo (NOK) |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Estado del Listing** | `validate_listing_status()` | Producto activo y comprable (`buyable` / `active`). | Listing pausado, suprimido o inactivo. |
| **2** | **Título** | `validate_title()` | Longitud entre **70 y 200** caracteres (Máx. estricto: 201 chars). | `< 70` chars (falta SEO) o `> 201` chars (riesgo supresión). |
| **3** | **Bullet Points** | `validate_bullet_points()` | Exactamente **5 bullets**, `≤ 250` chars c/u y formato **Emoji + MAYÚSCULAS + :**. | Cantidad `≠ 5`, `> 250` chars o formato incorrecto. |
| **4** | **Variaciones** | `validate_variations()` | **≥ 2** variaciones agrupadas (Parent-Child). | Menos de 2 variaciones en productos agrupados. |
| **5** | **Imágenes** | `validate_images()` | Carrusel principal con **7 a 10** imágenes. | `< 7` imágenes (incompleto) o `> 10` imágenes. |
| **6** | **Video** | `validate_video()` | Al menos **1 video** publicado en la galería. | 0 videos publicados. |
| **7** | **A+ Comparativa** | `validate_a_plus_comparative()` | Contenido A+ activo con **4-6 productos** en tabla + PVP + Reseñas. | Sin A+, `< 4` o `> 6` productos, o falta PVP/Reseñas. |
| **8** | **Reseñas Juntas** | `validate_reviews_sync()` | Mismo conteo acumulado de reseñas en todas las variantes. | Reseñas divididas o fragmentadas por variante. |

---

## 📁 Estructura del Proyecto

```
amazon_listings_validator/
├── app/
│   ├── main.py                   # Inicialización FastAPI, Lifespan y Health Check
│   ├── core/
│   │   └── config.py             # Parámetros BaseSettings, Credenciales LWA y Umbrales SOP
│   ├── models/
│   │   └── listing.py            # ListingValidationInput, N8nListingOutput y sanitizadores
│   ├── schemas/
│   │   └── validator.py          # EvaluationOutput y transformador to_n8n_output()
│   ├── services/
│   │   ├── sp_api_client.py      # Extractor asíncrono SP-API, Caché LWA y Fallbacks HTTP
│   │   └── validator.py          # Motor de reglas SOP deterministas
│   └── api/
│       └── v1/
│           └── validator.py      # Endpoints REST, Semáforo de concurrencia y Fallbacks
├── requirements.txt              # Dependencias de producción
├── Dockerfile                    # Configuración de contenedorización
└── README.md                     # Documentación técnica
```

---

## ⚙️ Componentes Principales

### 1. Motor de Reglas (`app/services/validator.py`)

Aplica las **8 reglas de negocio** del SOP Domy. Incluye soporte Unicode para expresiones regulares de viñetas:

- **Regex de Bullets:** Soporta emojis extended Unicode (`\U00010000-\U0010ffff`), variadores de estilo (`\ufe0f`) y conector ZWJ (`\u200d`).
- **Consolidación:** Concatena los errores detectados en la columna `Notas` separados por `|`. Si no hay fallos, asigna: `"Cumple 100% con SOP DOMY."`.

### 2. Cliente SP-API Híbrido (`app/services/sp_api_client.py`)

Combina múltiples estrategias de extracción para evitar vacíos de información:

- **LWA Authentication & Caching:** Controlado mediante `asyncio.Lock()` independiente por región (NA, EU, FE), renovando tokens automáticamente **60 segundos antes** de expirar.
- **Catalog API v2022-04-01:** Obtiene títulos, bullets, imágenes optimizadas y jerarquías Parent/Child.
- **A+ Content API v2020-11-01:** Inspecciona estructuras de tablas comparativas, conteo de ASINs expuestos y presencia de medios.
- **Resiliencia & Fallbacks:** Ante respuestas `403` o inconsistencias de API, conmuta a un **scraper público HTTP** sobre `/dp/{ASIN}` para rescatar métricas visuales del DOM.
- **Exponential Backoff:** Implementado con la librería `tenacity` para reintentar ante bloqueos de cuota `429` o fluctuaciones `5xx`.

### 3. Esquemas y Modelos Pydantic

- **`ListingValidationInput`** (`app/models/listing.py`): Modelo de entrada flexible. Normaliza booleanos (`"activo"`, `"buyable"`, `"1"` → `True`), convierte strings delimitados por `,` o `\n` en listas, y parsea arreglos de reseñas.
- **`N8nListingOutput`** (`app/models/listing.py`): Mapeo estricto 1:1 con alias exactos para los encabezados de Google Sheets (`ASIN`, `Listing Status`, `Titulo`, `Bullet Points`, `Variaciones`, `Imagenes Correctas`, `Video`, `A+ Comparativa`, `Reseñas Juntas`, `Notas`, `Fecha`).
- **`EvaluationOutput`** (`app/schemas/validator.py`): Capa intermedia que desacopla la lógica interna y provee el método `.to_n8n_output(asin)`.

### 4. Capa API REST (`app/api/v1/validator.py`)

- **Semáforo de Concurrencia:** Utiliza `asyncio.Semaphore(5)` para evitar sobrepasar la tasa de peticiones (Rate Limits) impuesta por Amazon.
- **Respuesta con Fallback Integrado (`_build_error_fallback`):** Si ocurren errores de red o infraestructura, la API **no se interrumpe**; retorna una estructura degradada `N8nListingOutput` detallando el fallo en la columna `Notas`.

---

## 🔄 Flujo de Automatización en n8n

El proceso mensual ejecuta un flujo iterativo optimizado para cuotas API:

1. **Schedule Trigger:** Se activa el **día 5 de cada mes**.
2. **Copy File:** Duplica la plantilla base creando un archivo fechado (`auditoria listing_YYYY-MM-DD-HH-MM.xlsx`).
3. **Matriz de Marketplaces:** Itera secuencialmente sobre **16 marketplaces** (ES, US, DE, FR, IT, UK, etc.).
4. **Loop Over Items (Batch Size = 1):** Procesa **una tienda a la vez**.
5. **Get Rows in Sheet:** Lee la lista maestra de ASINs desde `"Chequeo de estado listing Domy Company"`.
6. **Code JS & Sanitización:** Extrae y limpia la columna `ASIN`.
7. **Petición HTTP a Backend FastAPI:**
   - **Método:** `GET /api/v1/validator/fetch-and-evaluate/{marketplace}/{asin}`
   - **Configuración de Lote:** 3 ítems por lote con intervalo de **1800 ms** entre envíos y pausa previa de **0.8 s** (Wait Node).
8. **Manejo de Errores & Merge:** Reúne las ramas `Success` y `Error` preservando el número de fila original (`row_number`).
9. **Payload Bundler:** Consolida ~400 items procesados en un solo paquete masivo.
10. **Google Sheets Write:** Escribe la información completa en **una sola llamada por pestaña**, reduciendo de ~6,400 llamadas individuales a solo **16 escrituras masivas**.

---

## 🔐 Variables de Entorno y Configuración

```bash
# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================
ENVIRONMENT=production
LOG_LEVEL=INFO
ENABLE_MOCK=false

# ============================================================
# AMAZON SP-API - CREDENCIALES LWA
# ============================================================
SP_API_CLIENT_ID=amzn1.application-oa2-client.xxxxxxxxxxxxxxxxxxxx
SP_API_CLIENT_SECRET=amzn1.oa2-cs.v1.xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Refresh tokens independientes por región
SP_API_REFRESH_TOKEN_NA=Atzr|IwEBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SP_API_REFRESH_TOKEN_EU=Atzr|IwEBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SP_API_REFRESH_TOKEN_FE=Atzr|IwEBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ============================================================
# ENDPOINTS REGIONALES SP-API
# ============================================================
LWA_TOKEN_URL=https://api.amazon.com/auth/o2/token
REGION_ENDPOINTS_NA=https://sellingpartnerapi-na.amazon.com
REGION_ENDPOINTS_EU=https://sellingpartnerapi-eu.amazon.com
REGION_ENDPOINTS_FE=https://sellingpartnerapi-fe.amazon.com

# ============================================================
# LÍMITES DE CONCURRENCIA
# ============================================================
MAX_CONCURRENT_REQUESTS=10
REQUEST_TIMEOUT_SECONDS=30
MAX_RETRY_ATTEMPTS=3
```

---

## 🚀 Guía de Instalación y Despliegue

### Entorno de Desarrollo

```bash
# 1. Clonar repositorio
git clone https://github.com/domycompany/amazon_listings_validator.git
cd amazon_listings_validator

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# o
venv\Scripts\activate     # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con credenciales reales

# 5. Levantar servidor FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Entorno de Producción

```bash
# 1. Levantar servidor con workers
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# 2. Exponer vía Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8000

# 3. Verificar healthcheck
curl http://localhost:8000/health
```

---

## 📡 Documentación de Endpoints

### `GET /api/v1/validator/fetch-and-evaluate/{marketplace}/{asin}`

Extrae los datos del listing desde SP-API, aplica el motor de reglas SOP y retorna el resultado de la evaluación.

**Parámetros de Ruta:**

| Parámetro | Tipo | Descripción | Ejemplo |
| :--- | :--- | :--- | :--- |
| `marketplace` | string | Código o alias del marketplace | `US`, `MX`, `ES`, `IT`, `DE`, `FR` |
| `asin` | string | ASIN del producto (10 caracteres alfanuméricos) | `B08RNGL5SB` |

**Códigos de Respuesta HTTP:**

| Código | Significado | Causa Típica |
| :--- | :--- | :--- |
| **200 OK** | Validación exitosa | ASIN procesado y evaluado correctamente |
| **400 Bad Request** | Marketplace no soportado | Código no mapeado en `MARKETPLACE_IDS` |
| **403 Forbidden** | Sin autorización SP-API | Credenciales desactualizadas o seller sin permisos |
| **404 Not Found** | ASIN no encontrado | ASIN no existe o no está publicado |
| **429 Too Many Requests** | Rate limit alcanzado | Reintentar con backoff |
| **502 Bad Gateway** | Error de transporte | Fallo de conexión con SP-API o LWA |
| **503 Service Unavailable** | SP-API no disponible | Amazon SP-API retornó 5xx |

**Ejemplo de Request:**

```bash
curl -X GET "https://api.domycompany.com/api/v1/validator/fetch-and-evaluate/US/B08RNGL5SB" \
     -H "Authorization: Bearer <API_TOKEN>"
```

**Ejemplo de Response (200 OK):**

```json
{
  "ASIN": "B08RNGL5SB",
  "Listing Status": "Activo",
  "Titulo": "OK",
  "Bullet Points": "NOK",
  "Variaciones": "OK",
  "Imagenes Correctas": "OK",
  "Video": "OK",
  "A+ Comparativa": "OK",
  "Reseñas Juntas": "N/A",
  "Notas": "Bullets: Fallos en bullets: B2 sin formato 'Emoji + MAYÚSCULAS + :', B5 sin formato 'Emoji + MAYÚSCULAS + :'",
  "Fecha": "2026-09-21 19:28:46"
}
```

---

## 🛠️ Matriz de Solución de Problemas (Troubleshooting)

| Síntoma | Causa Probable | Solución |
| :--- | :--- | :--- |
| **Error 403 Forbidden** | Credenciales desactualizadas o seller sin autorización en el marketplace | Verificar `SP_API_REFRESH_TOKEN_EU`; regenerar token en Seller Central |
| **Error 429 Rate Limit** | Concurrencia demasiado alta | Reducir `MAX_CONCURRENT_REQUESTS` a 5; incrementar delay entre batches |
| **Timeout 504** | Batch demasiado grande | Reducir batch size en n8n; dividir lote por región |
| **Token LWA expirado** | Cache no se invalidó correctamente | Reiniciar servidor; forzar `force_refresh=true` |
| **A+ no detectado** | SP-API retorna 403 por falta de rol A+ | Verificar fallback HTML activo; revisar regex `aplus-v2` |
| **Video no detectado** | Catalog API no expone videos | Confirmar `_parse_video_from_html` aislado correctamente |
| **Comparativa A+ en 0** | Selectores CSS cambiaron | Revisar clases `apm-tablemodule` en HTML público |

---

**© 2026 Domy Company — Documentación Interna**
**Mantenedor:** Kelvin Williams - Data & Automation Engineer
**Última actualización:** Septiembre 2026