FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/stagecue.db \
    PORT=8000

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝应用代码
COPY . .

RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /data

EXPOSE 8000

# 不使用匿名卷以外的 VOLUME；compose 中把命名卷挂到 /data
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import json,os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health'%os.environ.get('PORT','8000'),timeout=3).read()" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
