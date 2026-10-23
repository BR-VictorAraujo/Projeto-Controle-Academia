# GymFlow web (Flask) — ambiente de desenvolvimento
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Fortaleza

# postgresql-client: pg_dump/pg_restore usados pelo modulo de backup
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

EXPOSE 5000
CMD ["python", "run.py"]
