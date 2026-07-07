FROM node:20-bookworm-slim

# System libraries Remotion's headless Chromium needs, plus ffmpeg for
# encoding and fonts (without fonts, all rendered text is empty boxes).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-dejavu-core \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libgbm1 \
    libasound2 \
    libxrandr2 \
    libxkbcommon0 \
    libxfixes3 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxcb1 \
    libpango-1.0-0 \
    libcairo2 \
    libcups2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund

# Pre-download the Chrome Headless Shell that @remotion/renderer uses, so the
# first render doesn't stall fetching it. Programmatic API (no CLI to resolve).
RUN node -e "import('@remotion/renderer').then(m => m.ensureBrowser()).then(() => console.log('chromium ready')).catch(e => { console.error(e); process.exit(1); })"

COPY . .

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["node", "generate.mjs"]
