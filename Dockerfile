# 播客工坊 Podcast Studio — 生产容器镜像
# 构建: docker build -t podcast-studio .
# 运行: docker compose up -d   (推荐,含数据卷与健康检查)
#
# 安全要点:
# - 以非 root 用户运行(容器逃逸面更小)
# - 数据目录固定 /data,由 compose 挂载持久化
# - 带 HEALTHCHECK,compose/k8s 能感知「进程活着但 API 挂了」
FROM python:3.12-slim

# ffmpeg 为音频处理必需;中文字体用于封面图 drawtext
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-cjk fontconfig curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户;UID/GID 可在构建时覆盖以匹配宿主机数据目录属主
ARG UID=10001
ARG GID=10001
RUN groupadd --gid "${GID}" app \
    && useradd --uid "${UID}" --gid "${GID}" --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml README.md MANIFEST.in ./
COPY podcast_studio ./podcast_studio
RUN pip install --no-cache-dir . && rm -rf /app/build

ENV PODCAST_STUDIO_HOME=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 数据目录归 app 所有(挂载卷首次创建时也继承该属主)
RUN mkdir -p /data && chown -R app:app /data /app
VOLUME /data
USER app
EXPOSE 8686

# 容器内只能走 0.0.0.0;Host 头校验默认只放行本机/局域网,
# 需要局域网访问时在 config 里显式设置 server.allow_lan=true
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8686/api/health || exit 1

CMD ["podcast-studio", "serve", "--host", "0.0.0.0", "--port", "8686"]
