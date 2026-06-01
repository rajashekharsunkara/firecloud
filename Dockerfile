FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Copy the full project into the container
COPY . .

# Install FireCloud using hatchling build system (pyproject.toml)
RUN pip install --no-cache-dir .

# Expose the default FireCloud node port
EXPOSE 7474

# Entrypoint: start the FireCloud node
ENTRYPOINT ["firecloud", "start"]
