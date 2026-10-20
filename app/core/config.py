from typing import Dict, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación cargada desde variables de entorno (.env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "Amazon Listings Validator API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # Credenciales SP-API (Obligatorias en .env)
    SP_API_CLIENT_ID: str
    SP_API_CLIENT_SECRET: str

    # Tokens de Refresh por Región
    SP_API_REFRESH_TOKEN_NA: Optional[str] = None
    SP_API_REFRESH_TOKEN_EU: Optional[str] = None
    SP_API_REFRESH_TOKEN_FE: Optional[str] = None

    # Refresh Token legacy / fallback global
    SP_API_REFRESH_TOKEN: Optional[str] = None

    LWA_TOKEN_URL: str = "https://api.amazon.com/auth/o2/token"

    # -------------------------------------------------------------------------
    # DICCIONARIOS Y CONSTANTES (Definidos mediante Field para Pydantic v2)
    # -------------------------------------------------------------------------

    # IDs Oficiales de Marketplace Amazon
    MARKETPLACE_IDS: Dict[str, str] = Field(
        default={
            # Europa / Medio Oriente
            "ES": "A1RKKUPIHCS9HS",
            "DE": "A1PA6795UKMFR9",
            "FR": "A13V1IB3VIYZZH",
            "IT": "APJ6JRA9NG5V4",
            "UK": "A1F83G8C2ARO7P",
            "NL": "A1805IZSGTT6HS",
            "SE": "A2NODRKZP88ZB9",
            "PL": "A1C3SOZRARQ6R3",
            "BE": "AMEN7PMS3EDWL",
            "IE": "A28R8C3THP2EG6",
            "TR": "A33AVAJ2PDY3EV",
            "AE": "A2VIGQ35RCS4UG",
            "SA": "A17E79C6D8DWNP",
            # Norteamérica
            "US": "ATVPDKIKX0DER",
            "CA": "A2EUQ1WTGCTBG2",
            "MX": "A1AM78C64UM0Y8",
        }
    )

    # Red de seguridad para alias/nombres de pestañas
    MARKETPLACE_ALIASES: Dict[str, str] = Field(
        default={
            "EEUU": "US",
            "USA": "US",
            "ESTADOS UNIDOS": "US",
            "CAD": "CA",
            "CANADA": "CA",
            "MEX": "MX",
            "MEXICO": "MX",
            "GB": "UK",
            "GRAN BRETAÑA": "UK",
            "UNITED KINGDOM": "UK",
            "ESP": "ES",
            "ESPAÑA": "ES",
            "GER": "DE",
            "ALEMANIA": "DE",
            "DEUTSCHLAND": "DE",
            "FRA": "FR",
            "FRANCIA": "FR",
            "ITA": "IT",
            "ITALIA": "IT",
        }
    )

    # Mapeo Código de País -> Región SP-API
    MARKETPLACE_TO_REGION: Dict[str, str] = Field(
        default={
            # Norteamérica (NA)
            "US": "NA",
            "CA": "NA",
            "MX": "NA",
            # Europa / Medio Oriente (EU)
            "ES": "EU",
            "DE": "EU",
            "FR": "EU",
            "IT": "EU",
            "UK": "EU",
            "NL": "EU",
            "SE": "EU",
            "PL": "EU",
            "BE": "EU",
            "IE": "EU",
            "TR": "EU",
            "AE": "EU",
            "SA": "EU",
            # Far East / Asia-Pacífico (FE)
            "JP": "FE",
            "AU": "FE",
        }
    )

    # Endpoints regionales de SP-API
    REGION_ENDPOINTS: Dict[str, str] = Field(
        default={
            "EU": "https://sellingpartnerapi-eu.amazon.com",
            "NA": "https://sellingpartnerapi-na.amazon.com",
            "FE": "https://sellingpartnerapi-fe.amazon.com",
        }
    )

    # Parámetros por defecto SOP Domy / Reglas de Validación
    DEFAULT_MIN_TITLE_LEN: int = 50
    DEFAULT_MAX_TITLE_LEN: int = 200
    OVERRIDE_MAX_TITLE_LEN: int = 201
    REQUIRED_BULLETS_COUNT: int = 5
    MAX_BULLET_CHARS: int = 250
    MIN_IMAGES_COUNT: int = 7
    MAX_IMAGES_COUNT: int = 10
    MIN_VARIATIONS_COUNT: int = 2
    MIN_APLUS_COMPARATIVE_PRODUCTS: int = 4
    MAX_APLUS_COMPARATIVE_PRODUCTS: int = 6

    # -------------------------------------------------------------------------
    # MÉTODOS DE HELPER Y RESOLUCIÓN
    # -------------------------------------------------------------------------

    def normalize_marketplace(self, marketplace: str) -> str:
        """Normaliza cualquier entrada (alias, código ISO, minúsculas) al código de dos letras oficial."""
        clean_mp = str(marketplace).strip().upper()
        return self.MARKETPLACE_ALIASES.get(clean_mp, clean_mp)

    def get_marketplace_id(self, marketplace: str) -> Optional[str]:
        """Obtiene el Marketplace ID de Amazon correspondiente."""
        code = self.normalize_marketplace(marketplace)
        return self.MARKETPLACE_IDS.get(code)

    def get_region(self, marketplace: str) -> str:
        """Determina la región SP-API ('NA', 'EU', 'FE') para un marketplace."""
        code = self.normalize_marketplace(marketplace)
        return self.MARKETPLACE_TO_REGION.get(code, "EU")

    def get_region_endpoint(self, marketplace: str) -> str:
        """Determina la URL del endpoint regional de SP-API según el país."""
        region = self.get_region(marketplace)
        return self.REGION_ENDPOINTS.get(region, self.REGION_ENDPOINTS["EU"])

    def get_refresh_token_for_marketplace(self, marketplace: str) -> Optional[str]:
        """Resuelve dinámicamente el Refresh Token correspondiente a la región del Marketplace,

        usando el token regional específico o cayendo en el fallback global.
        """
        region = self.get_region(marketplace)

        if region == "NA" and self.SP_API_REFRESH_TOKEN_NA:
            return self.SP_API_REFRESH_TOKEN_NA
        if region == "EU" and self.SP_API_REFRESH_TOKEN_EU:
            return self.SP_API_REFRESH_TOKEN_EU
        if region == "FE" and self.SP_API_REFRESH_TOKEN_FE:
            return self.SP_API_REFRESH_TOKEN_FE

        return self.SP_API_REFRESH_TOKEN


settings = Settings()