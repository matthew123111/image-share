FROM python:3.12-slim

WORKDIR /app

COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    UPLOAD_DIR=/data/uploads \
    MAX_UPLOAD_MB=25

EXPOSE 8000

CMD ["python", "/app/app.py"]
