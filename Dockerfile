FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gcc \
    postgresql-client \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies.
# Some environments intercept outbound HTTPS with an internal CA that is not in
# the default container trust store, so include the PyPI hosts explicitly as
# trusted endpoints to keep Docker builds reproducible in sandboxed CI.
COPY requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org -r requirements.txt

# Copy Alembic configuration and migrations for database commands
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Copy application code
COPY app/ ./app/
COPY agents/ ./agents/

# Expose port that FastAPI will run on
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
