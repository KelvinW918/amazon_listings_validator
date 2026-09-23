import contextlib
from fastapi import FastAPI
from app.core.config import settings
from app.api.v1.validator import router as validator_router
from app.services.sp_api_client import sp_api_client

import logging

# Configurar el formato y nivel global antes de instanciar FastAPI
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:\t%(asctime)s - %(message)s",
    datefmt="%H:%M:%S"
)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Lógica al iniciar la aplicación (Startup)
    yield
    # Lógica al apagar la aplicación (Shutdown): Cierre seguro de conexiones
    await sp_api_client.close()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Endpoint Validador
app.include_router(validator_router, prefix=settings.API_V1_STR)


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }