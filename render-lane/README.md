# render-lane

Remotion-based graphics generator for the station. Renders branded clips into
`./media/station/library/`, which ErsatzTV plays as filler / pre-roll.

## Compositions

| id | Output | Data source | ErsatzTV use |
|----|--------|-------------|--------------|
| `NowNextLater` | `library/junctions/now-next-later.mp4` | ErsatzTV schedule (now + next two) | junction / pre-roll filler |
| `FeaturePresentation` | `library/feature/feature-presentation.mp4` | current movie's title/rating/year/runtime from the schedule | pre-roll on the movie channel |
| `Ident` | `library/idents/ident.mp4` | static (env: name/tagline) | channel ident filler |

`generate.mjs` renders the `Ident` once at startup, then loops every `INTERVAL`
seconds re-rendering the two schedule-driven cards. All writes are atomic (temp
file + rename) so ErsatzTV never reads a half-written clip.

Movie metadata (rating, year) comes straight from ErsatzTV's XMLTV guide, which
already carries `<rating>` and `<date>` for films — no extra API/key needed.

## Config (env, set in docker-compose)

| Var | Default | Notes |
|-----|---------|-------|
| `ERSATZTV_URL` | `http://ersatztv:8409` | Internal URL on `media-network` |
| `CHANNEL` | `1` | Channel for now/next/later (number or name) |
| `MOVIE_CHANNEL` | `1` | Channel for the Feature Presentation card |
| `INTERVAL` | `300` | Seconds between re-renders |
| `ACCENT` | `#e50914` | Brand accent color (all cards) |
| `IDENT_NAME` | `My Channel` | Wordmark in the ident |
| `IDENT_TAGLINE` | (blank) | Optional tagline under the ident |
| `LOUDNORM_I` | `-27` | Card loudness target (LUFS) — see below |
| `LOUDNORM_TP` | `-1.5` | Max true peak (dBTP) |
| `LOUDNORM_LRA` | `11` | Loudness range |
| `LOUDNORM_FLOOR` | `-60` | At/below this measured LUFS a card counts as silent and its audio is left alone |

## Card loudness

Every rendered card gets a **two-pass `loudnorm`** after render and before it is
published: one measurement run, then a second that applies an exact linear gain.
Video is stream-copied, so it costs an audio re-encode (~1s), not a re-render.

This exists because a card's level otherwise tracks whatever rotating music bed
it happened to pick — junctions measured **-19.9 LUFS against programming at
-24.8**, i.e. ~5 LU hot, on every single junction. ErsatzTV does not fix this at
playout: its `NormalizeLoudnessMode` is deliberately `Off` so films keep their
dynamic range, which means each card has to arrive correct on its own.

Two details worth knowing before changing any of it:

- **`linear=true` matters.** Single-pass `loudnorm` is adaptive — it estimates
  as it goes, overshoots, and compresses. Two-pass linear applies one constant
  gain, so a card's own dynamics are untouched (verified: a 0.6 LU-range card
  stays 0.6 LU after correction).
- **The floor is not optional.** The Ident is a real AAC track carrying digital
  silence (-70 LUFS). Normalizing that would amplify the noise floor by ~40 dB,
  so anything under `LOUDNORM_FLOOR` is passed through untouched.

Keep these in step with `station-processor`'s matching vars so all filler —
rendered cards and ingested spots alike — lands on one level.

## Editing & styling — Remotion Studio

Studio is a live visual editor. Two ways to run it:

**A) On your Mac (best for real design work):**
```bash
cd render-lane
npm install
npx remotion studio        # http://localhost:3000
```
Edit `src/*.tsx` in your editor, preview instantly, then commit/push/deploy.

**B) In a browser on the server (accessible like the other containers):**
```bash
docker compose -f docker-compose.complete-homeserver.yml --profile studio up -d render-studio
# open http://<server-ip>:3000
```
This bind-mounts `./render-lane` into the container, so edits you make persist
to the repo **on the server** — commit them there (`git add/commit/push` on the
box). Stop it with `docker compose --profile studio down render-studio` (or just
`docker stop render-studio`) when you're done designing.

**Styling knobs** (all just React + CSS in `src/`):
- Colors/type/layout — inline styles in each composition.
- Logo — drop a PNG/SVG in `render-lane/public/`, use `<Img src={staticFile('logo.png')} />`.
- Fonts — add `@font-face` or `@remotion/google-fonts` (Liberation/DejaVu ship in the image).
- Motion — `spring()` (bouncy) / `interpolate()` (linear); `durationInFrames` in `Root.tsx` sets length.
- Sound — `<Audio src={staticFile('sting.mp3')} />`.

## Wiring into ErsatzTV

Point **Filler Presets** at the library folders:
- `library/junctions` → junction/pre-roll filler on the channel
- `library/idents` → channel ident filler
- `library/feature` → **pre-roll** filler on the movie channel/collection

> **v1 sync note:** schedule-driven cards render on a timer (accurate within
> `INTERVAL`), not as a unique clip pre-attached to each exact program boundary.
> Fine for a home channel; true per-item injection is a later enhancement.

## Build / run

```bash
docker compose -f docker-compose.complete-homeserver.yml up -d --build render-lane
docker logs -f render-lane
```
