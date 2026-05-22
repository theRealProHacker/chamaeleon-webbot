# Use the Python 3 official image
# https://hub.docker.com/_/python
FROM python:3.13-slim-bookworm

# Run in unbuffered mode
ENV PYTHONUNBUFFERED=1 

# Install German locale
RUN apt-get update && \
    apt-get install -y build-essential rustc cargo && \
    apt-get install -y locales && \
    sed -i '/de_DE.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen && \
    update-locale LANG=de_DE.UTF-8

# Set locale environment variables
ENV LANG=de_DE.UTF-8 \
    LANGUAGE=de_DE:de \
    LC_ALL=de_DE.UTF-8

# Create and change to the app directory.
WORKDIR /app

# Copy local code to the container image.
COPY . ./

# Install project dependencies
RUN python3 -m venv venv
RUN . venv/bin/activate && \
    pip install --upgrade pip && \
    pip install --no-cache-dir gunicorn && \
    pip install --no-cache-dir -r _requirements.txt

# Run the web service on container startup.
ENV WEB_CONCURRENCY=1 \
    WORKER_CONNECTIONS=1000 \
    GUNICORN_TIMEOUT=120

CMD ["/bin/bash", "-c", ". venv/bin/activate && exec gunicorn -k gevent --workers ${WEB_CONCURRENCY} --worker-connections ${WORKER_CONNECTIONS} --timeout ${GUNICORN_TIMEOUT} --bind 0.0.0.0:${PORT} app:app"]