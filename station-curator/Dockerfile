# station-curator: charter-driven programming for the ErsatzTV channels.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Zero dependencies by default. Gemini is called over plain REST with stdlib
# urllib, and everything else -- TOML parsing, SQLite, Jellyfin/Radarr/Sonarr/
# TMDB -- is stdlib too.
#
# Only the optional Anthropic backend needs a package. Build with
#   --build-arg WITH_ANTHROPIC=1
# if you switch CURATOR_LLM_PROVIDER to anthropic; without it that provider
# logs a warning and the curator runs deterministically.
ARG WITH_ANTHROPIC=0
RUN if [ "$WITH_ANTHROPIC" = "1" ]; then \
        pip install --no-cache-dir "anthropic>=1,<2"; \
    fi

COPY charters.toml ./
COPY curator ./curator

# ErsatzTV's database is mounted read-write (we edit collection membership and
# ordering). The curator's own state and shortlist live in /state.
VOLUME ["/state"]

CMD ["python", "-m", "curator"]
