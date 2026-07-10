FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Issue #36: Create non-root user early for security (before copying files)
RUN useradd -m -u 1000 -s /sbin/nologin kotolog

WORKDIR /app

# 依存をソースより先にコピー（レイヤーキャッシュ有効化）
COPY --chown=kotolog:kotolog pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

# ソースをコピーしてプロジェクト自体をインストール
COPY --chown=kotolog:kotolog src/ src/
RUN uv sync --no-dev --frozen

USER kotolog

# ビルド済み venv を直接使う（起動時に uv sync を走らせない）
ENV PATH="/app/.venv/bin:$PATH"

# Render は PORT 環境変数を自動で設定する
EXPOSE 8080
CMD ["sh", "-c", "uvicorn kotolog.line.webhook:app --host 0.0.0.0 --port ${PORT:-8080}"]
