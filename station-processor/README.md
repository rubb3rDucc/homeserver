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
    animation/     <- indie animation pulls, shorts and episodes mixed            (ROUTE)
  library/
    commercials/   -> processed individual spots      (point ErsatzTV filler here)
    bumpers/       -> processed normalized bumpers     (point ErsatzTV filler here)
    animation/     -> normalized shorts               (point ErsatzTV TAIL filler here)
  staging/
    animation/     -> episode-length pulls, untouched, waiting to be named + moved
                      into the Jellyfin TV library by hand
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
3. **Route** (`animation`) — a source that mixes shorts with episodes gets split
   by duration. At or under `ROUTE_MAX_SECONDS` it's an interstitial: normalized
   into `library/animation/` like any bumper. Over it, it's an episode: moved
   **untouched** to `staging/animation/`. Nothing re-encodes an episode down to
   filler spec, and nothing guesses at season/episode numbering — you name it and
   move it into the Jellyfin TV library yourself, after which it's an ordinary
   Jellyfin source in ErsatzTV with no station-specific handling at all.
4. **Normalize** — every output is re-encoded to `TARGET_WIDTH x TARGET_HEIGHT`
   at `TARGET_FPS` (aspect-preserving pad, no stretching) with EBU R128
   `loudnorm` to `LOUDNORM_I` LUFS. This consistency is what makes filler feel
   like part of the channel instead of a jarring cut.

   Loudness is **two-pass**: one measurement run, then a second run that applies
   an exact linear gain. Single-pass `loudnorm` is an *adaptive* filter — it
   estimates as it goes, overshoots its own target, and compresses on the way.
   That is why one-pass filler still sounds hot even when its stated `I` matches
   the programming. `linear=true` in pass 2 means a constant gain and no added
   compression, so a spot's own dynamics survive intact.

   Clips measuring below `LOUDNORM_FLOOR` are treated as silent and their audio
   is passed through untouched — idents are routinely silent by design, and
   "normalizing" digital silence just amplifies the noise floor by 40 dB.
5. **Archive** — the original is moved to `processed/` so it is never
   re-processed. Nothing is deleted. (A file staged as an episode is *moved*
   into `staging/`, so there is no original left to archive.)

## Tuning (env vars, set in docker-compose)

| Var | Default | Notes |
|-----|---------|-------|
| `POLL_INTERVAL` | `60` | Seconds between folder scans |
| `TARGET_WIDTH` / `TARGET_HEIGHT` | `1920` / `1080` | Output resolution |
| `TARGET_FPS` | `30` | Output framerate |
| `LOUDNORM_I` | `-27` | Target loudness (LUFS). See "Picking the target" below. |
| `LOUDNORM_TP` | `-1.5` | Max true peak (dBTP) |
| `LOUDNORM_LRA` | `11` | Loudness range |
| `LOUDNORM_FLOOR` | `-60` | At/below this measured LUFS a clip counts as silent and its audio is left alone |
| `MIN_SPOT_SECONDS` | `5` | Drop split fragments shorter than this |
| `MAX_SPOT_SECONDS` | `180` | Drop split fragments longer than this |
| `ROUTE_MAX_SECONDS` | `300` | Route lanes: at/under = interstitial, over = staged episode |
| `BLACK_MIN_DUR` | `0.10` | Min black duration (s) to count as a gap |
| `BLACK_PIX_TH` | `0.10` | Pixel blackness threshold (raise if gaps missed) |

If commercials aren't splitting, the black frames between them are probably
shorter/lighter than the defaults — lower `BLACK_MIN_DUR` or raise
`BLACK_PIX_TH` and re-drop the file.

### Picking the target

`LOUDNORM_I` is **not** a broadcast spec here. ATSC A/85 says -24 LUFS and EBU
R128 says -23, but both assume the *programming* is normalized to the same
number. Ours deliberately isn't: ErsatzTV's `NormalizeLoudnessMode` is left
`Off` so films keep their dynamic range, which means filler has to be matched to
whatever the library actually measures.

Sampled across 25 files (stereo downmix, as ErsatzTV outputs it), programming
runs **-18.3 to -31.7 LUFS, median -24.8** (TV -23.9, movies -25.5). That is a
13 LU spread, so no single filler level sits perfectly against all of it.

`-27` is that median with a ~2 LU trim. The trim is there because filler is
near-constant-level (junction cards measure 0.6 LU range) while programming is
not (median ~11, up to 24): at equal *integrated* loudness the flat clip is the
one that sounds louder, because the dynamic one spends most of its time below
its own average. Nudge toward `-25` if filler now feels too quiet.

To re-measure after the library changes:

```bash
ffmpeg -hide_banner -nostats -ss 420 -t 600 -i "$FILE" \
  -ac 2 -af ebur128=framelog=quiet -f null - 2>&1 | grep -E '^\s+(I|LRA):'
```

The remaining mismatch is the programming being inconsistent *with itself*, and
only normalizing the source files can fix that — offline, two-pass, `linear=true`
so it's a constant gain and no dynamic range is lost.

## Build / run

Built and run as part of the main compose file:

```bash
docker compose -f docker-compose.complete-homeserver.yml up -d --build station-processor
```

Watch it work:

```bash
docker logs -f station-processor
```
