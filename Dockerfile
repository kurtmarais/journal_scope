# Journal Scope server, for hosting it so a phone can reach it from anywhere.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN grep -v pywebview requirements.txt > req.txt && pip install --no-cache-dir -r req.txt \
 && python -m playwright install --with-deps chromium
COPY app ./app
ENV JOURNAL_SCOPE_CACHE=/tmp/journal_scope
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
