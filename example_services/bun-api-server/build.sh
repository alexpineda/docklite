#!/bin/bash

# Create and use a new builder instance that supports multi-arch builds
docker buildx create --use

# Build for AMD64 and push
docker buildx build \
  --platform linux/amd64 \
  -t registry.digitalocean.com/api-alexpineda-containers/bun-example-api \
  --push .