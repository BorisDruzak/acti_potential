FROM python:3.12-slim-bookworm
ARG SOURCE_SHA=development
LABEL org.opencontainers.image.revision=$SOURCE_SHA
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
RUN groupadd -g 10001 acti && useradd -u 10001 -g acti -M acti
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
