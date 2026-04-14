# Use the official Python image from Docker Hub
FROM python:3.13

# Set the working directory
WORKDIR /cm-mcp/ask-uos-mcp

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    wget \
    gnupg \
    cmake \
    pkg-config \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin:$PATH"

# Copy pyproject.toml first for dependency caching
COPY pyproject.toml ./

# Install dependencies
RUN uv sync

# Copy log_conf from root
COPY log_conf ./log_conf

# Copy source code from ask-uos-mcp directory
COPY ask-uos-mcp/. .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/cm-mcp/ask-uos-mcp/.venv/bin:$PATH"

# Expose the port
EXPOSE 8001

# Run the server
#CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--log-level", "info"]
