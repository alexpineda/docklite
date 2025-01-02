# Use Bun's official image
FROM oven/bun:1

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