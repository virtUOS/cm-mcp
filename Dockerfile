# Use the official Python image from Docker Hub
FROM python:3.13

# Set the working directory
WORKDIR /cm-mcp


# Copy the project
COPY . .


# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    wget \
    gnupg \
    git \
    cmake \
    vim \
    pkg-config \
    python3-dev \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*




# Install dependencies
# Install uv
# Download the latest installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh
# Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv sync 

ENV PATH="/cm-mcp/.venv/bin:$PATH"

# Install dependencies
# COPY ./requirements.txt .
# RUN uv pip install --system -r requirements.txt



EXPOSE 8000


