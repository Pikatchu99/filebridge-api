FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# No system build toolchain needed: every dependency (including psycopg[binary]) ships
# a prebuilt wheel for this image's platform, so there's nothing to compile.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
