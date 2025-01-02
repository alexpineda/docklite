# Use Bun's official image (Debian variant)
FROM oven/bun:debian

# Set the working directory
WORKDIR /app

# Copy package files
COPY package.json ./

# Copy source code
COPY src ./src

# Install dependencies
RUN bun install

# Expose the port your app listens on
EXPOSE 3000

# Command to run your app
CMD ["bun", "src/index.ts"]