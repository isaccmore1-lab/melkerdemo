FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
ENV ERP_V2_DATA_DIR=/tmp/erp_v2_demo
ENV DEMO_RESET_ON_START=1
CMD ["python", "demo_server.py"]
