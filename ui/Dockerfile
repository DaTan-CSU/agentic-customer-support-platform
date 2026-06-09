# Dev-mode image — runs `next dev` so HMR works against the mounted source.
# Production build is intentionally out of scope for this demo.
FROM node:20-alpine

WORKDIR /app

# Install deps using whatever lockfile happens to be present. pnpm-lock takes
# precedence if both exist; npm is the default elsewhere in the project.
COPY package.json package-lock.json* pnpm-lock.yaml* ./

RUN if [ -f pnpm-lock.yaml ]; then \
        corepack enable && pnpm install --frozen-lockfile; \
    else \
        npm install; \
    fi

# Source. Compose mounts the project read-write for HMR in dev.
COPY . ./

EXPOSE 3000

# Use dev:next directly — the `dev` script also tries to spawn a uvicorn
# child, which we don't want inside this container (backend is its own
# service in compose).
CMD ["npm", "run", "dev:next"]
