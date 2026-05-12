FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY src/ ./src/
COPY main.py .

# Crear directorios de trabajo
RUN mkdir -p data/raw data/processed data/mibel_outputs figures results

# Punto de entrada
ENTRYPOINT ["python", "main.py"]
CMD ["--skip-download", "--step", "all"]
