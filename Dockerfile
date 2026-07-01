FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV CONFIG_PATH=/config/config.yaml

EXPOSE 8080

USER 65534

CMD ["python", "-m", "bird_exporter.main"]
