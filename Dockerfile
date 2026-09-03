# syntax=docker/dockerfile:1

FROM node:22-slim AS build
WORKDIR /app

# Install dependencies — generates package-lock.json if not present
COPY package.json package-lock.json* ./
RUN npm install --ignore-scripts

# Build the app -> produces .output/ (Nitro node-server bundle + public assets)
COPY . .
RUN npm run build

# ---- Runtime stage ----
FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
# Cloud Run injects PORT; Nitro's node-server preset honours it. Default to 8080.
ENV PORT=8080

# Only the build output is needed to run the server.
COPY --from=build /app/.output ./.output

EXPOSE 8080
CMD ["node", ".output/server/index.mjs"]
