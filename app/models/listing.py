from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict, AliasChoices, field_validator


class ListingValidationInput(BaseModel):
    """Payload de entrada flexible y tolerante a fallos para Swagger, n8n, Sheets y SP-API."""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore"
    )

    asin: str = Field(..., validation_alias=AliasChoices('asin', 'ASIN'))
    marketplace: str = Field(default="ES")

    # Permitir bool o str, normalizado a Optional[bool] (None representa 'Estado no verificado / N/A')
    is_active: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices('is_active', 'Listing Status', 'status', 'listing_status')
    )

    title: str = Field(..., validation_alias=AliasChoices('title', 'Titulo', 'titulo'))

    bullet_points: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices('bullet_points', 'Bullet Points', 'bullets')
    )

    variation_asins: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices('variation_asins', 'variaciones', 'Variaciones', 'variations')
    )

    image_urls: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices('image_urls', 'Imagenes Correctas', 'imagenes', 'images')
    )

    # Campos opcionales: None indica 'Dato no disponible en la API / N/A'
    has_video: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices('has_video', 'Video', 'video')
    )

    # Datos de A+ Content
    has_a_plus: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices('has_a_plus', 'A+ Comparativa', 'a_plus')
    )
    a_plus_comparison_products_count: Optional[int] = Field(default=None)
    a_plus_has_price: Optional[bool] = Field(default=None)
    a_plus_has_reviews: Optional[bool] = Field(default=None)

    # Reseñas por variante
    variant_review_counts: Optional[List[int]] = Field(
        default=None,
        validation_alias=AliasChoices('variant_review_counts', 'resenas_juntas', 'Reseñas Juntas', 'reviews')
    )

    title_max_length_override: Optional[int] = Field(None)

    # -------------------------------------------------------------------------
    # VALIDADORES DE TOLERANCIA A DATOS CRUDOS DE N8N / GOOGLE SHEETS
    # -------------------------------------------------------------------------

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_is_active(cls, v: Any) -> Optional[bool]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            clean_v = v.strip().lower()
            if clean_v in ("true", "1", "activo", "active", "buyable", "yes", "si", "sí", "ok"):
                return True
            if clean_v in ("false", "0", "inactivo", "inactive", "no", "nok"):
                return False
            raise ValueError(f"Valor de is_active no reconocido: {v!r}")
        return bool(v)

    @field_validator("bullet_points", "variation_asins", "image_urls", mode="before")
    @classmethod
    def parse_string_to_list(cls, v: Any) -> List[str]:
        """Convierte cadenas separadas por comas o saltos de línea en listas de Python."""
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item).strip() for item in v if item is not None and str(item).strip()]
        if isinstance(v, str):
            delimiter = "\n" if "\n" in v else ","
            return [item.strip() for item in v.split(delimiter) if item.strip()]
        return []

    @field_validator("variant_review_counts", mode="before")
    @classmethod
    def parse_reviews_to_list(cls, v: Any) -> Optional[List[int]]:
        """Convierte cadenas de reseñas (ej: '45, 120') en listas de enteros."""
        if v is None or v == "":
            return None
        if isinstance(v, list):
            return [int(x) for x in v if str(x).isdigit()]
        if isinstance(v, str):
            items = v.replace("[", "").replace("]", "").split(",")
            return [int(x.strip()) for x in items if x.strip().isdigit()]
        return None


class N8nListingOutput(BaseModel):
    """Esquema estricto de salida 1:1 para N8N."""
    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True
    )

    asin: str = Field(..., alias="ASIN")
    listing_status: str = Field(..., alias="Listing Status")
    titulo: str = Field(..., alias="Titulo")
    bullet_points: str = Field(..., alias="Bullet Points")
    variaciones: str = Field(..., alias="Variaciones")
    imagenes_correctas: str = Field(..., alias="Imagenes Correctas")
    video: str = Field(..., alias="Video")
    a_plus_comparativa: str = Field(..., alias="A+ Comparativa")
    resenas_juntas: str = Field(..., alias="Reseñas Juntas")
    notas: str = Field("", alias="Notas")
    fecha: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        alias="Fecha"
    )

# ========================================================================
# MODELOS PARA LA FASE 2: AUDITORÍA DE VISIÓN E INTELIGENCIA ARTIFICIAL
# ========================================================================

class ImageVisionAnalysys(BaseModel):
    """Resultados detallados del análisis multimodal / visión sobre las imágenes del listing."""
    is_main_background_white: bool = Field(
        default=True,
        description="Indica si la imagen principal tiene fondo blanco puro"
    )