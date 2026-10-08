import re
import unicodedata
from typing import List, Tuple
from app.core.config import settings
from app.models.listing import ListingValidationInput, N8nListingOutput

# =========================================================================
# REGEX DE FORMATO DE BULLET POINTS
# =========================================================================
# Soporta:
#   - Múltiples emojis consecutivos (banderas 🇮🇹, ZWJ 👨‍👩‍👧, símbolos 🛡️)
#   - Letras acentuadas de ES / IT / FR / DE
#   - Símbolos comunes: guiones, paréntesis, slashes, apóstrofes
#
# Formato esperado: <emoji(s)> <ESPACIO> <MAYÚSCULAS>: <texto>
# =========================================================================
BULLET_FORMAT_REGEX = re.compile(
    r'^(?:'
    r'[\U0001F000-\U0001FFFF'   # Emojis principales (banderas, caritas, símbolos)
    r'\u2600-\u27BF'             # Símbolos misc + dingbats
    r'\u2100-\u214F'             # Letterlike symbols
    r'\u2190-\u21FF'             # Flechas
    r'\u2300-\u23FF'             # Técnicos (⏱ ⏰ etc.)
    r'\u2B00-\u2BFF'             # Misc symbols and arrows (⭐)
    r'\uFE0F'                     # Variation Selector-16
    r'\u200D'                     # Zero-Width Joiner
    r']'
    r')+'
    r'\s+'
    r'([A-Z0-9\s'
    r'ÁÉÍÓÚÑÜ'                    # Español
    r'ÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÇŒÆ'         # Italiano / Francés / Alemán
    r'\-–—'
    r'_()/.&\'`´'
    r']+?'
    r'):'
    r'\s',
    re.UNICODE
)


# =========================================================================
# REGLA 1 — ESTADO DEL LISTING
# =========================================================================
def validate_listing_status(status_input: bool | str | None) -> Tuple[str, str]:
    """Regla 1: Activo / Inactivo / N/A según disponibilidad real."""
    if status_input is None:
        return "N/A", "Estado del listing no verificable (sin datos)."

    if isinstance(status_input, str):
        active = status_input.strip().lower() in {
            "activo", "active", "true", "buyable", "yes", "si", "sí", "ok"
        }
    else:
        active = bool(status_input)

    return (
        ("Activo", "Listing activo.")
        if active
        else ("Inactivo", "Listing inactivo o no comprable.")
    )


# =========================================================================
# REGLA 2 — TÍTULO
# =========================================================================
def validate_title(title: str | None, max_override: int | None = None) -> Tuple[str, str]:
    """Regla 2: Título en rango 70-200 chars (o override) con normalización Unicode."""
    clean = unicodedata.normalize("NFC", title or "").strip()
    min_len = settings.DEFAULT_MIN_TITLE_LEN
    max_len = max_override if max_override is not None else settings.DEFAULT_MAX_TITLE_LEN
    length = len(clean)

    if length == 0:
        return "NOK", "Título vacío o no recuperado."
    if min_len <= length <= max_len:
        return "OK", f"Longitud correcta ({length} caracteres)."
    return "NOK", f"Longitud fuera de rango ({length} chars. Permitido: {min_len}-{max_len})."


# =========================================================================
# REGLA 3 — BULLET POINTS
# =========================================================================
def validate_bullet_points(bullets: List[str]) -> Tuple[str, str]:
    """Regla 3: Exactamente 5 bullets, ≤ 250 chars y formato Emoji + MAYÚSCULAS + :"""
    if len(bullets) != settings.REQUIRED_BULLETS_COUNT:
        return "NOK", f"Cantidad incorrecta de bullets ({len(bullets)} de 5 requeridos)."

    errors = []
    for idx, bullet in enumerate(bullets, 1):
        bullet_clean = bullet.strip()
        if len(bullet_clean) > settings.MAX_BULLET_CHARS:
            errors.append(f"B{idx} excede 250 chars ({len(bullet_clean)})")
        if not BULLET_FORMAT_REGEX.match(bullet_clean):
            errors.append(f"B{idx} sin formato 'Emoji + MAYÚSCULAS + :'")

    if errors:
        return "NOK", f"Fallos en bullets: {', '.join(errors)}"
    return "OK", "5 bullets cumplen formato y longitud SOP."


# =========================================================================
# REGLA 4 — VARIACIONES
# =========================================================================
def validate_variations(variations: List[str]) -> Tuple[str, str]:
    """Regla 4: Presencia de 2+ opciones agrupadas o N/A para productos simples."""
    count = len(variations)
    if count >= settings.MIN_VARIATIONS_COUNT:
        return "OK", f"Tiene {count} variaciones vinculadas."
    elif count <= 1:
        return "N/A", "Producto individual sin variaciones."
    return "NOK", f"Variaciones insuficientes ({count}). Se requieren al menos {settings.MIN_VARIATIONS_COUNT}."


# =========================================================================
# REGLA 5 — IMÁGENES
# =========================================================================
def validate_images(images: List[str]) -> Tuple[str, str]:
    """Regla 5: Carrusel principal con 7 a 10 imágenes."""
    count = len(images)
    if settings.MIN_IMAGES_COUNT <= count <= settings.MAX_IMAGES_COUNT:
        return "OK", f"Carrusel óptimo ({count} imágenes)."
    return "NOK", f"Imágenes fuera de rango ({count} de 7-10)."


# =========================================================================
# REGLA 6 — VIDEO
# =========================================================================
def validate_video(has_video: bool | None) -> Tuple[str, str]:
    """Regla 6: Presencia de al menos 1 video publicado."""
    if has_video is None:
        return "N/A", "Sin datos de video en SP-API."
    if has_video:
        return "OK", "Video publicado detectado."
    return "NOK", "Listing sin video publicado."


# =========================================================================
# REGLA 7 — A+ COMPARATIVA
# =========================================================================
def validate_a_plus_comparative(
    has_a_plus: bool | None,
    comp_count: int | None,
    has_price: bool | None,
    has_reviews: bool | None,
) -> Tuple[str, str]:
    """Regla 7: Cuadro comparativo A+ con 4-6 productos hermanos + PVP + reseñas."""
    if has_a_plus is None:
        return "N/A", "Sin datos de A+ en SP-API."
    if not has_a_plus:
        return "NOK", "Sin contenido A+ configurado."

    comp_count_val = comp_count or 0
    has_price_val = bool(has_price)
    has_reviews_val = bool(has_reviews)

    issues = []
    if not (
        settings.MIN_APLUS_COMPARATIVE_PRODUCTS
        <= comp_count_val
        <= settings.MAX_APLUS_COMPARATIVE_PRODUCTS
    ):
        issues.append(f"Productos en comparativa fuera de rango ({comp_count_val} de 4-6)")
    if not has_price_val:
        issues.append("Falta PVP en la tabla comparativa")
    if not has_reviews_val:
        issues.append("Faltan reseñas en la tabla comparativa")

    if issues:
        return "NOK", f"A+ Incompleto: {', '.join(issues)}"
    return "OK", f"A+ Comparativa válida ({comp_count_val} productos)."


# =========================================================================
# REGLA 8 — RESEÑAS JUNTAS
# =========================================================================
def validate_reviews_sync(
    review_counts: List[int] | None,
    variation_asins: List[str] | None,
) -> Tuple[str, str]:
    """
    Regla 8: Verifica la unificación de reseñas en productos con variaciones.

    Lógica de evaluación:
    1. Si NO hay variaciones (producto único) -> N/A (la regla no aplica).
    2. Si HAY variaciones (familia Parent/Child con 2+ ASINs):
       a. Si tenemos datos numéricos de reseñas por variante:
          - Si todos los conteos son iguales -> OK (reseñas unificadas).
          - Si hay discrepancia -> NOK (reseñas fragmentadas).
       b. Si NO tenemos datos numéricos (SP-API no expone el conteo por variante):
          - Asumir OK por estar en una familia con variaciones.
            Amazon unifica las reseñas a nivel de familia cuando el seller
            ha configurado correctamente el Parent/Child. La ausencia de
            métricas individuales no debe penalizar al listing.
    """

    # 1. Si no tiene variaciones, la regla de "reseñas juntas" no aplica
    has_variations = bool(variation_asins and len(variation_asins) > 1)
    if not has_variations:
        return "N/A", "Producto individual sin variaciones."

    # 2. Si tiene variaciones y SÍ recibimos datos de reseñas desde la API
    if review_counts is not None and len(review_counts) > 0:
        unique_counts = set(review_counts)

        if len(unique_counts) == 1:
            return "OK", f"Reseñas unificadas ({review_counts[0]} en todas las variantes)."

        return "NOK", f"Reseñas no consolidadas entre variantes: {review_counts}"

    # 3. Si tiene variaciones pero NO tenemos la métrica individual de reviews por SP-API,
    #    asumir OK por estar en una familia con variaciones unificadas.
    #    La ausencia de datos no debe penalizar al listing.
    return (
        "OK",
        f"Familia con {len(variation_asins)} variantes. "
        f"Reseñas unificadas por defecto (sin desglose por SP-API).",
    )


# =========================================================================
# MOTOR PRINCIPAL
# =========================================================================
def evaluate_listing(input_data: ListingValidationInput) -> N8nListingOutput:
    """Aplica las 8 reglas sobre los datos recibidos (Hardened)."""
    status_val, status_note = validate_listing_status(input_data.is_active)
    title_status, title_note = validate_title(
        input_data.title, input_data.title_max_length_override
    )
    bullets_status, bullets_note = validate_bullet_points(input_data.bullet_points)
    vars_status, vars_note = validate_variations(input_data.variation_asins)
    imgs_status, imgs_note = validate_images(input_data.image_urls)
    video_status, video_note = validate_video(input_data.has_video)
    aplus_status, aplus_note = validate_a_plus_comparative(
        input_data.has_a_plus,
        input_data.a_plus_comparison_products_count,
        input_data.a_plus_has_price,
        input_data.a_plus_has_reviews,
    )
    reviews_status, reviews_note = validate_reviews_sync(
        input_data.variant_review_counts,
        input_data.variation_asins,
    )

    failures = []

    # Registrar fallo si el listing está inactivo
    if status_val == "Inactivo":
        failures.append(f"Listing Status: {status_note}")

    checks = [
        ("Titulo", title_status, title_note),
        ("Bullets", bullets_status, bullets_note),
        ("Variaciones", vars_status, vars_note),
        ("Imagenes", imgs_status, imgs_note),
        ("Video", video_status, video_note),
        ("A+ Comparativa", aplus_status, aplus_note),
        ("Reseñas", reviews_status, reviews_note),
    ]

    for name, st, note in checks:
        if st == "NOK":
            failures.append(f"{name}: {note}")

    notas = " | ".join(failures) if failures else "Cumple 100% con SOP DOMY."

    return N8nListingOutput(
        asin=input_data.asin,
        listing_status=status_val,
        titulo=title_status,
        bullet_points=bullets_status,
        variaciones=vars_status,
        imagenes_correctas=imgs_status,
        video=video_status,
        a_plus_comparativa=aplus_status,
        resenas_juntas=reviews_status,
        notas=notas,
    )