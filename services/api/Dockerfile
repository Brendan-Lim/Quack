FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUACK_ENV=production \
    QUACK_AUTH_MODE=supabase \
    QUACK_BUDGET_PROJECT_USD=0 \
    QUACK_BUDGET_HOURLY_USD=0 \
    QUACK_BUDGET_SESSION_USD=0 \
    QUACK_BUDGET_REQUEST_USD=0 \
    QUACK_LEDGER_PATH=/data/usage.jsonl

WORKDIR /srv
COPY services/api/requirements.txt services/api/requirements.lock /build/
RUN pip install --no-cache-dir -r /build/requirements.txt -c /build/requirements.lock \
    && groupadd --gid 10001 quack \
    && useradd --uid 10001 --gid quack --no-create-home quack \
    && mkdir /data && chown quack:quack /data
COPY --chown=quack:quack services/api/quack/ /srv/quack/
USER quack
EXPOSE 8000
CMD ["uvicorn", "quack.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
