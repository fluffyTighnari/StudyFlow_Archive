# 整个应用一个镜像：FastAPI 后端 + 静态前端（同源，一个端口）。
#
# 前端没有构建步骤（纯 ES module），所以这里只装 Python 依赖、把两个目录拷进去。
# 容器里用 `tools.serveui --dist`，它就是本项目本地开发用的那条启动路径 ——
# 不另写一份 "生产启动器"，避免两套启动逻辑漂移。

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MOUXUE_SKIP_ENV_FILE=0

WORKDIR /app

# 依赖表只有一处（backend/pyproject.toml）。先装依赖再加代码，利用层缓存。
# 用 -e：非 editable 会在 /app/backend 落一份 build/ 副本，并把 app/tools
# 复制进 site-packages —— 面上跑的是哪一份代码就说不清了。
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
COPY backend/tools /app/backend/tools
RUN pip install --no-cache-dir -e "/app/backend[seed]"

# 前端是静态根；题目/材料用仓库自带的夹具题库。
COPY frontend/redesign /app/frontend/redesign
COPY backend/tests/fixtures /app/backend/tests/fixtures

# 入口脚本负责"空库先播种"——否则 fake 替身找不到计划会 exit 2。
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint
RUN chmod +x /usr/local/bin/docker-entrypoint \
    && mkdir -p /data

EXPOSE 8125

WORKDIR /app/backend

ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]
