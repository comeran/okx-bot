FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY src ./src
COPY config ./config

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "src.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
