FROM python:3-slim
RUN pip install --no-cache-dir requests
WORKDIR /app
# rank.py is shared with the librarian — one definition of "which release is
# best for the PS5", used both when choosing what to fetch and what to link.
COPY librarian/rank.py watchlist/poller.py ./
CMD ["python3", "-u", "poller.py"]
