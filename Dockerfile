FROM python:3.14-rc-slim
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY serviceline_backbone /app/serviceline_backbone
COPY config /app/config
RUN pip install --no-cache-dir .
CMD ["uvicorn", "serviceline_backbone.main:app", "--host", "0.0.0.0", "--port", "8000"]
