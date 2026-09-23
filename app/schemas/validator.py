from pydantic import BaseModel, ConfigDict, Field
from app.models.listing import N8nListingOutput


class EvaluationOutput(BaseModel):
    """Esquema interno con el resultado individual de cada regla de negocio."""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore"
    )

    listing_status: str = Field(..., description="Activo / Inactivo")
    title_eval: str = Field(..., description="OK / NOK / N/A")
    bullets_eval: str = Field(..., description="OK / NOK / N/A")
    variations_eval: str = Field(..., description="OK / NOK / N/A")
    images_eval: str = Field(..., description="OK / NOK / N/A")
    video_eval: str = Field(default="N/A", description="OK / NOK / N/A")
    aplus_eval: str = Field(..., description="OK / NOK / N/A")
    reviews_eval: str = Field(..., description="OK / NOK / N/A")
    notes: str = Field(default="", description="Consolidado de hallazgos y advertencias")

    def to_n8n_output(self, asin: str) -> N8nListingOutput:
        """Helper para transformar el resultado interno directamente a la estructura de n8n."""
        return N8nListingOutput(
            ASIN=asin,
            **{
                "Listing Status": self.listing_status,
                "Titulo": self.title_eval,
                "Bullet Points": self.bullets_eval,
                "Variaciones": self.variations_eval,
                "Imagenes Correctas": self.images_eval,
                "Video": self.video_eval,
                "A+ Comparativa": self.aplus_eval,
                "Reseñas Juntas": self.reviews_eval,
                "Notas": self.notes,
            }
        )