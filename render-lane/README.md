# render-lane

Remotion-based graphics generator for the station. Renders branded clips from
schedule/metadata into `./media/station/library/`, which ErsatzTV plays as
filler / pre-roll.

**First composition: `NowNextLater`** — a "Coming up… Now / Next / Later"
junction card built from the live ErsatzTV schedule. Idents and MTV nameplates
will be added as additional compositions on the same base.

## How it works

```
ErsatzTV XMLTV guide  ->  generate.mjs (parse -> now/next/later)
                            -> Remotion render (src/NowNextLater.tsx)
                            -> /station/library/junctions/now-next-later.mp4
```

`generate.mjs` loops every `INTERVAL` seconds: it fetches
`${ERSATZTV_URL}/iptv/xmltv.xml`, finds the configured channel, works out which
programme is on now (plus the next two), renders the card with that data, and
writes it atomically (temp file + rename) so ErsatzTV never reads a
half-written clip.

## Config (env, set in docker-compose)

| Var | Default | Notes |
|-----|---------|-------|
| `ERSATZTV_URL` | `http://ersatztv:8409` | Internal URL on `media-network` |
| `CHANNEL` | `1` | ErsatzTV channel number **or** display-name |
| `INTERVAL` | `300` | Seconds between re-renders |
| `ACCENT` | `#e50914` | Brand accent color |
| `OUTPUT` | `/station/library/junctions/now-next-later.mp4` | Where the card lands |

## Wiring it into ErsatzTV

Point a **Filler Preset** (or a channel's pre-roll/junction filler) at
`./media/station/library/junctions`. The card refreshes every `INTERVAL`
seconds so it reflects the current schedule.

> **Known limitation (v1):** this renders a single "current" card on a timer,
> not a unique card pre-attached to each specific junction. It's accurate to
> within `INTERVAL`, which is fine for a home channel. True per-junction cards
> (a distinct clip injected ahead of each program boundary) are a later
> enhancement once the base pipeline is proven.

## Local development (optional, outside Docker)

```bash
cd render-lane
npm install
npx remotion studio        # live-preview/edit the card with defaultProps
npm run generate           # run the real fetch+render loop (needs ErsatzTV reachable)
```

## Build / run

```bash
docker compose -f docker-compose.complete-homeserver.yml up -d --build render-lane
docker logs -f render-lane
```
