# Use the Python 3 official image
# https://hub.docker.com/_/python
FROM python:3

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
CMD ["/bin/bash", "-c", ". venv/bin/activate && exec gunicorn app:app"]