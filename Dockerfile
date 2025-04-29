# Use an official Python runtime as a parent image
# Using python 3.11 to match environment where errors occurred, slim is smaller
FROM python:3.11-slim

# Set environment variables
# Prevents Python from writing pyc files to disc (popular in containers)
ENV PYTHONDONTWRITEBYTECODE 1
# Ensures Python output is sent straight to terminal (useful for logging)
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies if needed (psql client not strictly needed for connector)
# RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Copy only requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
# Ensure .dockerignore is set up correctly to avoid copying unnecessary files
COPY . .

# Expose the port the app runs on (Cloud Run injects PORT env var, usually 8080)
# Gunicorn will bind to the port specified by the $PORT env var
EXPOSE 8080

# Define the command to run the application using Gunicorn
# Binds to all interfaces on the port specified by PORT (provided by Cloud Run)
# Uses 1 worker, let Cloud Run manage scaling/concurrency via multiple instances
# Uses multiple threads per worker (adjust number as needed)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "0", "main:app"]

# Alternative using PORT env var directly (Cloud Run injects PORT=8080 by default):
# CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--workers", "1", "--threads", "8", "--timeout", "0", "main:app"]
