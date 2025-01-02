# Use Node.js base image
FROM node:18-slim

# Install bun
RUN apt-get update && apt-get install -y curl unzip \
    && curl -fsSL https://bun.sh/install | bash

# Add bun to PATH
ENV PATH="/root/.bun/bin:${PATH}"

# Set the working directory
WORKDIR /app

# Copy package.json and bun.lockb (if exists)
COPY package.json ./

# Install dependencies
RUN bun install

# Copy source code
COPY src ./src

# Expose the port your app listens on
EXPOSE 3000

# Command to run your app
CMD ["bun", "src/index.ts"]