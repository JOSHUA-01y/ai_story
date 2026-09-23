# Debian 11 (bullseye) 的 LTS 已于 2026-08 结束，deb.debian.org 上的 bullseye-security
# 已被撤下，apt 安装 libmariadb3 / default-libmysqlclient-dev 会 404。
# 因此基础镜像改用仍受支持的 Debian 12 (bookworm)；Python 保持 3.11，
# 因为 Django 3.2.15 无法在 Python 3.12+ 上运行（distutils 已被移除）。
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 可选：把 Debian 源换成国内镜像（例如 mirrors.ustc.edu.cn），国内网络下 apt 会快很多。
# 通过 --build-arg APT_MIRROR=... 或 compose 的 build.args 传入；留空则使用官方源。
ARG APT_MIRROR=

RUN set -eu; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g" "$f"; fi; \
      done; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    gcc \
    libc6-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

COPY pyproject.toml uv.lock ./

# 可选：指定 PyPI 镜像（例如 https://mirrors.cloud.tencent.com/pypi/simple）加速依赖下载。
# 注意 uv.lock 里 81 个依赖的源被固定为清华 tuna 镜像，uv 会按锁文件里的 URL 取包，
# 单靠 UV_DEFAULT_INDEX 无法覆盖，因此这里同时把锁定的源改写为 PYPI_INDEX。
# 改写只发生在构建镜像内，仓库中的 uv.lock 不会被修改。
ARG PYPI_INDEX=

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    set -eu; \
    if [ -n "$PYPI_INDEX" ]; then \
      export UV_DEFAULT_INDEX="$PYPI_INDEX" PIP_INDEX_URL="$PYPI_INDEX"; \
      index_base="${PYPI_INDEX%/simple}"; \
      sed -i "s|https://pypi.tuna.tsinghua.edu.cn|$index_base|g" uv.lock; \
    fi; \
    pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

COPY backend /app/backend
COPY scripts /app/scripts

RUN AI_STORY_COMPILE_CLOSED_SOURCE=1 /opt/venv/bin/python /app/backend/manage.py setup_in_docker \
    && sed -i 's/\r$//' /app/scripts/check_closed_source_apps.sh \
    && /app/scripts/check_closed_source_apps.sh

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

ARG APT_MIRROR=

RUN set -eu; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g" "$f"; fi; \
      done; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/backend /app/backend
COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint

RUN sed -i 's/\r$//' /usr/local/bin/backend-entrypoint \
    && chmod +x /usr/local/bin/backend-entrypoint \
    && mkdir -p staticfiles media logs

EXPOSE 8010

ENTRYPOINT ["/usr/local/bin/backend-entrypoint"]
CMD ["gunicorn", "--chdir", "/app/backend", "config.wsgi:application", "--bind", "0.0.0.0:8010"]
