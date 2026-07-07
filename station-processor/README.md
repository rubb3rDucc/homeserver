# station-processor

Ingest pipeline that turns raw filler footage into channel-ready spots for
ErsatzTV. Splits commercial compilations at their black-frame gaps and
normalizes loudness + resolution/fps so nothing blasts or letterboxes on air.

Pure `ffmpeg` + Python standard library — no Python packages to install.

## Folder layout (`./media/station/`)

```
station/
  incoming/
    commercials/   <- drop raw compilations, RetroJunk clips, Pinchflat pulls here (SPLIT)
    bumpers/       <- drop raw idents/bumpers here                               (NORMALIZE)
  library/
    commercials/   -> processed individual spots      (point ErsatzTV filler here)
    bumpers/       -> processed normalized bumpers     (point ErsatzTV filler here)
  processed/       -> originals archived after processing (safe to delete by hand)
  logs/processor.log
  musicvideos/     <- Pinchflat music videos (not processed here; nameplate step is later)
```

## How it works

1. **Watch** — polls `incoming/*` every `POLL_INTERVAL` seconds. A file is only
   picked up once its size has been stable for one cycle, so in-flight
   downloads are never grabbed half-written.
2. **Split** (`commercials`) — `ffmpeg blackdetect` finds the black frames
   between spots; the content spans between them become individual clips (black
   trimmed off). Fragments shorter than `MIN_SPOT_SECONDS` or longer than
   `MAX_SPOT_SECONDS` are dropped (and logged). If no black gaps are found, the
   whole file is kept as one clip with a warning.
3. **Normalize** — every output is re-encoded to `TARGET_WIDTH x TARGET_HEIGHT`
   at `TARGET_FPS` (aspect-preserving pad, no stretching) with EBU R128
   `loudnorm` to `LOUDNORM_I` LUFS. This consistency is what makes filler feel
   like part of the channel instead of a jarring cut.
4. **Archive** — the original is moved to `processed/` so it is never
   re-processed. Nothing is deleted.

## Tuning (env vars, set in docker-compose)

| Var | Default | Notes |
|-----|---------|-------|
| `POLL_INTERVAL` | `60` | Seconds between folder scans |
| `TARGET_WIDTH` / `TARGET_HEIGHT` | `1920` / `1080` | Output resolution |
| `TARGET_FPS` | `30` | Output framerate |
| `LOUDNORM_I` | `-16` | Target loudness (LUFS). Match your shows. |
| `LOUDNORM_TP` | `-1.5` | Max true peak (dBTP) |
| `LOUDNORM_LRA` | `11` | Loudness range |
| `MIN_SPOT_SECONDS` | `5` | Drop split fragments shorter than this |
| `MAX_SPOT_SECONDS` | `180` | Drop split fragments longer than this |
| `BLACK_MIN_DUR` | `0.10` | Min black duration (s) to count as a gap |
| `BLACK_PIX_TH` | `0.10` | Pixel blackness threshold (raise if gaps missed) |

If commercials aren't splitting, the black frames between them are probably
shorter/lighter than the defaults — lower `BLACK_MIN_DUR` or raise
`BLACK_PIX_TH` and re-drop the file.

## Build / run

Built and run as part of the main compose file:

```bash
docker compose -f docker-compose.complete-homeserver.yml up -d --build station-processor
```

Watch it work:

```bash
docker logs -f station-processor
```
