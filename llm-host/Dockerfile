FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY llm-host/requirements.txt /tmp/llm-host-requirements.txt
COPY stock-mcp/requirements.txt /tmp/stock-mcp-requirements.txt
COPY marketplace-mcp/requirements.txt /tmp/marketplace-mcp-requirements.txt

RUN python -m pip install --no-cache-dir \
    -r /tmp/llm-host-requirements.txt \
    -r /tmp/stock-mcp-requirements.txt \
    -r /tmp/marketplace-mcp-requirements.txt

COPY llm-host /app/llm-host
COPY stock-mcp /app/stock-mcp
COPY marketplace-mcp /app/marketplace-mcp

WORKDIR /app/llm-host

EXPOSE 8000
CMD ["uvicorn", "web_api:app", "--host", "0.0.0.0", "--port", "8000"]
