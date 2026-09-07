FROM python:3.11-slim

WORKDIR /app

# build-essential needed for packages that compile from source (e.g. some
# xgboost/scipy wheels on certain platforms)
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV MODEL_BASE_PATH=/app/models

EXPOSE 8080
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
