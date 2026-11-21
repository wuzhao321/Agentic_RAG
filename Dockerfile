FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.11-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" ANONYMIZED_TELEMETRY=False
COPY pyproject.toml uv.lock requirements-backend.txt ./
RUN uv sync --frozen --no-dev --extra models --no-install-project \
    && uv pip install -r requirements-backend.txt
COPY src ./src
COPY configs ./configs
COPY scripts/download_models.py scripts/bootstrap_hosted.py ./scripts/
RUN uv pip install --no-deps . \
    && mkdir -p data models
EXPOSE 8780
CMD ["kb", "api"]
