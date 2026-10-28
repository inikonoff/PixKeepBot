FROM python:3.13-slim

# exiftool is required for lossless metadata stripping
RUN apt-get update && apt-get install -y --no-install-recommends \
    libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# run as an unprivileged user
RUN useradd --create-home --uid 10001 pixkeep && chown -R pixkeep /app
USER pixkeep

CMD ["python", "main.py"]
