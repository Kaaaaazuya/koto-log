FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# 依存をソースより先にコピー（レイヤーキャッシュ有効化）
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

# ソースをコピーしてプロジェクト自体をインストール
COPY src/ src/
RUN uv sync --no-dev --frozen

# Render は PORT 環境変数を自動で設定する
EXPOSE 8080
CMD ["sh", "-c", "uv run uvicorn kotolog.line.webhook:app --host 0.0.0.0 --port ${PORT:-8080}"]
