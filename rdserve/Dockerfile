FROM python:3-slim
RUN pip install --no-cache-dir requests
WORKDIR /app
COPY rdserve/server.py ./
CMD ["python3", "-u", "server.py"]
