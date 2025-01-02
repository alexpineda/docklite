# Use Bun's official image (Debian variant)
FROM --platform=linux/amd64 oven/bun:debian

# Set the working directory
WORKDIR /app

# Copy package files
COPY package.json ./
COPY bun.lockb ./

# Copy source code
COPY src ./src

# Install dependencies
RUN bun install

# Expose the port your app listens on
EXPOSE 3000

# Command to run your app
CMD ["bun", "src/index.ts"]