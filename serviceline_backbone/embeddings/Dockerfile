FROM python:3.14-slim

RUN pip install fastapi uvicorn sentence-transformers

COPY app.py /app/app.py

WORKDIR /app

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
