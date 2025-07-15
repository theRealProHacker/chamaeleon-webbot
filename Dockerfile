# Use the Python 3 official image
# https://hub.docker.com/_/python
FROM python:3

# Run in unbuffered mode
ENV PYTHONUNBUFFERED=1 

# Create and change to the app directory.
WORKDIR /app

# Copy local code to the container image.
COPY . ./

# Install project dependencies
RUN python3 -m venv venv
RUN . venv/bin/activate
RUN pip install --no-cache-dir -r requirements.txt

# Run the web service on container startup.
["/bin/bash", "-c", ". venv/bin/activate && exec gunicorn app:app"]