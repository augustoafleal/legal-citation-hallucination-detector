FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY run.sh ./run.sh
RUN pip install --no-cache-dir . && chmod +x run.sh

ENTRYPOINT ["bash", "run.sh"]
