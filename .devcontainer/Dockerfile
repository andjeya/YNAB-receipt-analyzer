FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

RUN apt-get update \
    && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
        build-essential \
        bubblewrap \
        curl \
        ca-certificates \
        git \
        jq \
        redis-tools \
        sqlite3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
