# Imagen base ligera de Python
FROM python:3.11-slim

# Directorio de trabajo
WORKDIR /app

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Instalar dependencias del sistema si fueran necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements o pyproject.toml y instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente del proyecto
COPY . .

# Exponer puerto de Uvicorn
EXPOSE 8000

# Comando de ejecución con reload opcional o producción
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]