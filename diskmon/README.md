# diskmon

Disk space monitor for the homeserver stack. Watches host disk usage and automatically pauses all torrents in qBittorrent when space runs low, then resumes them once space is freed. Sends phone notifications via [ntfy](https://ntfy.sh) on state changes.

## Why

If the host disk fills up, downloads keep running until services start breaking. diskmon prevents this by pausing torrents before it's too late and notifying you so you can clean up.

## Architecture

```
┌─── Host Machine ───────────────────────────────────────────────────┐
│                                                                    │
│  / (root filesystem)                                               │
│  │                                                                 │
│  └──mounted read-only──┐                                           │
│                        ▼                                           │
│  ┌─── Docker ──────────────────────────────────────────────────┐   │
│  │                                                             │   │
│  │  ┌─── diskmon container ─────────────────────────────────┐  │   │
│  │  │                                                       │  │   │
│  │  │  main.go                                              │  │   │
│  │  │  ┌──────────────────────────────────────────────┐     │  │   │
│  │  │  │           Ticker Loop (every 5m)             │     │  │   │
│  │  │  │                                              │     │  │   │
│  │  │  │  ┌────────────┐    ┌──────────────────────┐  │     │  │   │
│  │  │  │  │ monitor.go │    │ State: paused=bool   │  │     │  │   │
│  │  │  │  │            │    │                      │  │     │  │   │
│  │  │  │  │ Statfs()   │───▶│ usage >= threshold?  │  │     │  │   │
│  │  │  │  │ on /hostfs │    │                      │  │     │  │   │
│  │  │  │  └────────────┘    └──────┬───────┬───────┘  │     │  │   │
│  │  │  │                      yes  │       │  no      │     │  │   │
│  │  │  │              (& !paused)  │       │ (& paused)     │  │   │
│  │  │  │                           ▼       ▼          │     │  │   │
│  │  │  │              ┌─────────────┐ ┌────────────┐  │     │  │   │
│  │  │  │              │ Pause All   │ │ Resume All │  │     │  │   │
│  │  │  │              └──────┬──────┘ └─────┬──────┘  │     │  │   │
│  │  │  │                     │              │         │     │  │   │
│  │  │  │                     ▼              ▼         │     │  │   │
│  │  │  │              ┌──────────────────────────┐    │     │  │   │
│  │  │  │              │        notify.go         │    │     │  │   │
│  │  │  │              │  Send state transition   │    │     │  │   │
│  │  │  │              │  notification            │    │     │  │   │
│  │  │  │              └────────────┬─────────────┘    │     │  │   │
│  │  │  └───────────────────────────┼──────────────────┘     │  │   │
│  │  │                              │                        │  │   │
│  │  └──────────────────────────────┼────────────────────────┘  │   │
│  │                                 │                           │   │
│  │          ┌──────────────────────┼────────┐                  │   │
│  │          │                      │        │                  │   │
│  │          ▼                      ▼        │                  │   │
│  │  ┌──────────────┐    ┌──────────────┐    │                  │   │
│  │  │   gluetun    │    │  Shoutrrr    │    │                  │   │
│  │  │  (VPN gate)  │    │              │    │                  │   │
│  │  │              │    │  ntfy / tg / │    │                  │   │
│  │  │ ┌──────────┐ │    │  discord /   │    │                  │   │
│  │  │ │qBittorr- │ │    │  slack / ... │    │                  │   │
│  │  │ │ent API   │ │    └──────┬───────┘    │                  │   │
│  │  │ │:8080     │ │           │            │                  │   │
│  │  │ └──────────┘ │           │       media-network           │   │
│  │  └──────────────┘           │            │                  │   │
│  │                             │            │                  │   │
│  └─────────────────────────────┼────────────┘──────────────────┘   │
│                                │                                   │
└────────────────────────────────┼───────────────────────────────────┘
                                 ▼
                          ┌─────────────┐
                          │  Your Phone │
                          │  (ntfy app) │
                          └─────────────┘
```

## How It Works

1. Every 5 minutes (configurable), checks disk usage on the host filesystem
2. If usage >= 85% (configurable) and torrents are running — pauses all torrents via the qBittorrent API and sends a notification
3. If usage drops below the threshold and torrents were paused — resumes all torrents and sends a notification
4. Only notifies on state transitions, so you won't get spammed

The host root filesystem is mounted read-only into the container at `/hostfs`, so `Statfs("/hostfs")` reports actual host disk usage rather than the container overlay.

## Configuration

All configuration is done through environment variables:

| Variable | Default | Description |
|---|---|---|
| `DISKMON_CHECK_PATH` | `/hostfs` | Path to check inside the container |
| `DISKMON_THRESHOLD` | `85` | Disk usage percentage that triggers a pause |
| `DISKMON_INTERVAL` | `5m` | How often to check (Go duration: `30s`, `5m`, `1h`) |
| `DISKMON_QB_URL` | `http://gluetun:8080` | qBittorrent Web API URL |
| `DISKMON_QB_USERNAME` | *(empty)* | qBittorrent username (if auth is enabled) |
| `DISKMON_QB_PASSWORD` | *(empty)* | qBittorrent password (if auth is enabled) |
| `DISKMON_NTFY_URL` | *(empty)* | Shoutrrr notification URL (e.g. `ntfy://ntfy.sh/my-topic`) |

## Setup

### 1. Configure environment variables

Copy the variables from `.env.template` into your `.env` file and fill in your values:

```env
DISKMON_THRESHOLD=85
DISKMON_INTERVAL=5m
DISKMON_QB_USERNAME=admin
DISKMON_QB_PASSWORD=your-password
DISKMON_NTFY_URL=ntfy://ntfy.sh/your-topic-here
```

### 2. Start the service

```bash
# Build and start diskmon
docker compose -f docker-compose.complete-homeserver.yml up -d --build diskmon

# View logs
docker compose -f docker-compose.complete-homeserver.yml logs -f diskmon
```

### 3. Verify it's working

Logs should show periodic disk usage readings:

```
diskmon started: path=/hostfs threshold=85% interval=5m0s
Disk usage: 62.3% (threshold: 85%)
```

## Testing

To test the pause/notification flow without waiting for your disk to actually fill up:

```bash
# Set a very low threshold so it triggers immediately
DISKMON_THRESHOLD=1 docker compose -f docker-compose.complete-homeserver.yml up diskmon
```

This will:
- Report disk usage (which will be above 1%)
- Pause all torrents in qBittorrent
- Send a notification to your phone via ntfy

Reset the threshold back to `85` and restart to resume normal operation.

## Docker Compose Service

The service is defined in `docker-compose.complete-homeserver.yml`:

```yaml
diskmon:
  build: ./diskmon
  container_name: diskmon
  environment:
    - DISKMON_CHECK_PATH=/hostfs
    - DISKMON_THRESHOLD=${DISKMON_THRESHOLD:-85}
    - DISKMON_INTERVAL=${DISKMON_INTERVAL:-5m}
    - DISKMON_QB_URL=http://gluetun:8080
    - DISKMON_QB_USERNAME=${DISKMON_QB_USERNAME:-}
    - DISKMON_QB_PASSWORD=${DISKMON_QB_PASSWORD:-}
    - DISKMON_NTFY_URL=${DISKMON_NTFY_URL:-}
  volumes:
    - /:/hostfs:ro
  depends_on:
    - gluetun
  restart: unless-stopped
  networks:
    - media-network
```

Key details:
- `volumes: /:/hostfs:ro` — mounts the host root filesystem read-only so disk checks reflect actual host usage
- `depends_on: gluetun` — ensures the VPN gateway (and qBittorrent behind it) is available
- `restart: unless-stopped` — automatically restarts on failure or host reboot

## Notifications

diskmon uses [Shoutrrr](https://containrrr.dev/shoutrrr/) for notifications, which supports many services beyond ntfy. Some examples:

```env
# ntfy (recommended)
DISKMON_NTFY_URL=ntfy://ntfy.sh/your-topic

# Telegram
DISKMON_NTFY_URL=telegram://token@telegram?channels=channel-1

# Discord
DISKMON_NTFY_URL=discord://token@id

# Slack
DISKMON_NTFY_URL=slack://hook:token@workspace/channel
```

See the [Shoutrrr docs](https://containrrr.dev/shoutrrr/v0.8/services/overview/) for all supported services.
