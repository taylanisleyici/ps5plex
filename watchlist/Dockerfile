FROM python:3-slim
RUN pip install --no-cache-dir requests guessit
WORKDIR /app
# rank.py is shared with the librarian - one definition of "best release".
COPY librarian/rank.py watchlist/poller.py watchlist/picker.py ./
# The picker is the default: you choose the source, like Stremio.
CMD ["python3", "-u", "picker.py"]
