/**
 * Render lane orchestrator.
 *
 * At startup: render the static channel Ident once.
 * On an interval: fetch the ErsatzTV XMLTV guide and render
 *   - a NowNextLater junction card for CHANNEL, and
 *   - a FeaturePresentation card for the current/next movie on MOVIE_CHANNEL,
 * pulling title/year/rating/runtime straight from the guide.
 *
 * ErsatzTV then plays these as filler / pre-roll.
 */
import { bundle } from "@remotion/bundler";
import { selectComposition, renderMedia, openBrowser } from "@remotion/renderer";
import { XMLParser } from "fast-xml-parser";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import http from "node:http";
import { createReadStream } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const ERSATZTV_URL = process.env.ERSATZTV_URL || "http://ersatztv:8409";
const asList = (v) =>
  (v || "").split(",").map((s) => s.trim()).filter(Boolean);
// Comma-separated channel lists -> one card each. Empty = that card is skipped.
const CHANNELS = asList(process.env.CHANNEL); // now/next/later channels
const MOVIE_CHANNELS = asList(process.env.MOVIE_CHANNEL); // feature-card channels
const INTERVAL = parseInt(process.env.INTERVAL || "300", 10);
const ACCENT = process.env.ACCENT || "#e50914";
// Cap the cores a single render grabs so a burst never pegs the whole box
// (Remotion otherwise defaults to ~all cores). ErsatzTV needs the rest for
// live transcoding.
const CONCURRENCY = parseInt(process.env.RENDER_CONCURRENCY || "2", 10);

// Chromium's GL backend. Remotion's default is null, which emits no --use-gl
// flag at all -> headless Chrome falls back to SwiftShader and rasterizes every
// gradient, shadow and filter on the CPU. "angle-egl" uses the real GPU *and*
// switches on VaapiVideoDecoder, which is what decodes the b-roll clips. Needs
// /dev/dri passed into the container; set RENDER_GL=swangle to force software
// again if the GPU is missing or the driver misbehaves.
const RENDER_GL = process.env.RENDER_GL || "angle-egl";
const CHROMIUM_OPTIONS = { gl: RENDER_GL === "default" ? null : RENDER_GL };

// OffthreadVideo pulls one b-roll frame per rendered frame via a separate seek;
// a larger cache means far fewer repeat seeks into the same clip. Bytes.
const OFFTHREAD_CACHE = parseInt(
  process.env.OFFTHREAD_CACHE_BYTES || String(512 * 1024 * 1024),
  10
);

// These are 15-second filler clips that ErsatzTV re-transcodes on the way out,
// so trading a little encoder efficiency for speed is free in practice.
const X264_PRESET = process.env.X264_PRESET || "veryfast";

// Cards play between programming that is NOT loudness-normalized — ErsatzTV's
// NormalizeLoudnessMode is deliberately Off so films keep their dynamic range —
// so each card has to arrive at the right level on its own. Left alone, a card
// lands wherever its rotating music bed sits, which measured 6-10 LU hotter
// than the shows on either side of it. Target is measured from the library
// (see README), not a broadcast spec.
const LOUDNORM_I = process.env.LOUDNORM_I || "-27";
const LOUDNORM_TP = process.env.LOUDNORM_TP || "-1.5";
const LOUDNORM_LRA = process.env.LOUDNORM_LRA || "11";
// Below this measured level the card is treated as silent and left alone. The
// Ident is silent by design, and "normalizing" digital silence would just
// amplify the noise floor.
const LOUDNORM_FLOOR = parseFloat(process.env.LOUDNORM_FLOOR || "-60");

// Optional CN City assets for the junction card. Any URL Remotion can fetch, or
// a staticFile() path under public/. Empty -> the card uses its CSS fallback.
// BROLL_SRC pins one clip; otherwise a random clip from BROLL_DIR plays each
// render (drop .mp4/.webm/.mov files in ./media/station/broll on the host).
const BROLL_SRC = process.env.BROLL_SRC || "";
const BROLL_DIR = process.env.BROLL_DIR || "/station/broll";
const MUSIC_SRC = process.env.MUSIC_SRC || "";
const MUSIC_DIR = process.env.MUSIC_DIR || "/station/music";
const ASSET_PORT = parseInt(process.env.ASSET_PORT || "8788", 10);
const VOICEOVER_SRC = process.env.VOICEOVER_SRC || "";
const VIDEO_RE = /\.(mp4|webm|mov|mkv)$/i;
const AUDIO_RE = /\.(mp3|m4a|aac|ogg|opus|wav|flac)$/i;

// Jellyfin: source each token's artwork (show Logo, else poster) by title.
// Reachable over the shared media-network as http://jellyfin:8096. Needs an API
// key (Jellyfin dashboard -> API Keys). No key -> tokens fall back to initials.
const JELLYFIN_URL = process.env.JELLYFIN_URL || "http://jellyfin:8096";
const JELLYFIN_API_KEY = process.env.JELLYFIN_API_KEY || "";

const NOW_DIR = process.env.NOW_DIR || "/station/library/junctions";
const FEATURE_DIR = process.env.FEATURE_DIR || "/station/library/feature";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- Asset rotation (B-roll + music) -------------------------------------- //
// OffthreadVideo / Audio need http URLs, so serve the asset folders over
// loopback. Chromium renders in this same container, so 127.0.0.1 is reachable.
// URLs look like http://127.0.0.1:PORT/<kind>/<file> where kind = broll|music.
const ASSET_DIRS = { broll: BROLL_DIR, music: MUSIC_DIR };

function startAssetServer() {
  const server = http.createServer((req, res) => {
    const parts = decodeURIComponent((req.url || "").split("?")[0])
      .replace(/^\//, "")
      .split("/");
    const dir = ASSET_DIRS[parts[0]];
    const name = parts[1];
    if (!dir || !name || name.includes("..") || !(VIDEO_RE.test(name) || AUDIO_RE.test(name))) {
      res.writeHead(404);
      return res.end();
    }
    const stream = createReadStream(path.join(dir, name));
    stream.on("error", () => {
      res.writeHead(404);
      res.end();
    });
    res.writeHead(200, {
      "Content-Type": AUDIO_RE.test(name) ? "audio/mpeg" : "video/mp4",
    });
    stream.pipe(res);
  });
  server.on("error", (e) => console.warn("asset server error:", e.message));
  server.listen(ASSET_PORT, "127.0.0.1", () =>
    console.log(`asset server on http://127.0.0.1:${ASSET_PORT} (broll=${BROLL_DIR} music=${MUSIC_DIR})`)
  );
  return server;
}

// Pick a random file for this render (re-listed each time, so uploads are picked
// up). `explicit` (BROLL_SRC/MUSIC_SRC) pins one instead. Empty -> asset unused.
async function pickAsset(kind, dir, re, explicit) {
  if (explicit) return explicit;
  try {
    const files = (await fs.readdir(dir)).filter((f) => re.test(f));
    if (!files.length) return "";
    const pick = files[Math.floor(Math.random() * files.length)];
    return `http://127.0.0.1:${ASSET_PORT}/${kind}/${encodeURIComponent(pick)}`;
  } catch {
    return ""; // dir missing -> asset simply unused
  }
}
const pickBroll = () => pickAsset("broll", BROLL_DIR, VIDEO_RE, BROLL_SRC);
const pickMusic = () => pickAsset("music", MUSIC_DIR, AUDIO_RE, MUSIC_SRC);

// ---- XMLTV helpers -------------------------------------------------------- //
function parseXmltvTime(s) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?/.exec(
    String(s).trim()
  );
  if (!m) return null;
  const [, Y, Mo, D, H, Mi, S, tz] = m;
  const offset = tz ? `${tz.slice(0, 3)}:${tz.slice(3)}` : "Z";
  const d = new Date(`${Y}-${Mo}-${D}T${H}:${Mi}:${S}${offset}`);
  return isNaN(d.getTime()) ? null : d;
}

const text = (v) => (v && typeof v === "object" ? v["#text"] : v);
const arr = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);

function fmtRuntime(min) {
  if (!min || min <= 0) return "";
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}

// The guide is a single document covering every channel, but getSchedule() is
// called once per channel — so a 5-channel cycle was fetching and re-parsing the
// same XML five times. Fetch and parse once per cycle; renderCards() clears it.
let guideCache = null;
async function fetchGuide() {
  if (guideCache) return guideCache;
  const res = await fetch(`${ERSATZTV_URL}/iptv/xmltv.xml`);
  if (!res.ok) throw new Error(`XMLTV fetch failed: HTTP ${res.status}`);
  const xml = await res.text();
  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "@_",
  });
  guideCache = parser.parse(xml).tv || {};
  return guideCache;
}

async function getSchedule(channel) {
  const tv = await fetchGuide();

  const channels = arr(tv.channel);
  const match = channels.find((c) => {
    const id = String(c["@_id"] ?? "");
    const names = arr(c["display-name"]).map((n) => String(text(n)));
    return id === channel || names.includes(channel);
  });
  const channelId = match ? String(match["@_id"]) : channel;
  const channelName = match
    ? String(text(arr(match["display-name"])[0]) ?? channel)
    : channel;

  const items = arr(tv.programme)
    .filter((p) => String(p["@_channel"]) === channelId)
    .map((p) => {
      const start = parseXmltvTime(p["@_start"]);
      const stop = parseXmltvTime(p["@_stop"]);
      const ratingRaw = arr(p.rating)[0];
      const rating = ratingRaw ? String(text(ratingRaw.value ?? ratingRaw) ?? "") : "";
      const runtime =
        start && stop ? Math.round((stop - start) / 60000) : 0; // minutes
      return {
        title: String(text(p.title) ?? "Untitled"),
        start,
        stop,
        year: p.date ? String(text(p.date)).slice(0, 4) : "",
        rating,
        runtime,
        specs: featureSpecs(p),
      };
    })
    .filter((p) => p.start && p.stop)
    .sort((a, b) => a.start - b.start);

  return { channelName, items };
}

function currentIndex(items) {
  const now = new Date();
  let idx = items.findIndex((p) => p.start <= now && now < p.stop);
  if (idx === -1) {
    idx = items.findIndex((p) => p.start > now);
    if (idx === -1) idx = items.length - 1;
  }
  return idx;
}

// The upcoming programme (the feature the pre-roll precedes). Falls back to the
// current one if nothing is scheduled after now.
function nextIndex(items) {
  const now = new Date();
  const idx = items.findIndex((p) => p.start > now);
  return idx === -1 ? currentIndex(items) : idx;
}

// Best-effort format/caption/language spec rows from XMLTV. ErsatzTV may not
// populate these; when empty, the card shows the classic HBO trio by default.
function featureSpecs(p) {
  const specs = [];
  const audio = String(text(p.audio?.stereo) ?? "").toLowerCase();
  const subs = arr(p.subtitles);
  const langs = [text(p.language), text(p["orig-language"]), ...subs.map((s) => s && s.language)]
    .map((x) => String(x ?? "").toLowerCase());
  if (/surround|5\.1|dolby|digital/.test(audio))
    specs.push({ code: "dolby", lines: ["DOLBY DIGITAL 5.1 SOUND", "WHERE AVAILABLE"] });
  if (subs.length) specs.push({ code: "cc", lines: ["CLOSED CAPTIONED"] });
  if (langs.some((l) => /span|español|\besp?\b/.test(l)))
    specs.push({ code: "esp", lines: ["EN ESPAÑOL"] });
  return specs;
}

function pickNowNextLater(items) {
  const idx = currentIndex(items);
  const fmt = (d) =>
    d ? d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
  const at = (i) =>
    items[i]
      ? { title: items[i].title, time: fmt(items[i].start) }
      : { title: "—", time: "" };
  return { now: at(idx), next: at(idx + 1), later: at(idx + 2) };
}

// ---- Jellyfin token artwork ----------------------------------------------- //
// Match a programme title to a Jellyfin item and return an image for the round
// token: its Logo, else its poster for the rare show with no logo. Returns
// { url, type: "logo"|"poster" }. Cached by title (art is stable).
const tokenCache = new Map();
async function resolveToken(title) {
  const empty = { url: "", type: "" };
  if (!JELLYFIN_API_KEY || !title || title === "—") return empty;
  if (tokenCache.has(title)) return tokenCache.get(title);

  let result = empty;
  try {
    const q = new URLSearchParams({
      searchTerm: title,
      IncludeItemTypes: "Series,Movie",
      Recursive: "true",
      Limit: "1",
      api_key: JELLYFIN_API_KEY,
    });
    const res = await fetch(`${JELLYFIN_URL}/Items?${q}`);
    if (res.ok) {
      const item = ((await res.json()).Items || [])[0];
      // Use the title Logo (rendered with a dark keyline outline so even white
      // wordmarks read on the cream disc). Only fall back to the poster / key
      // art for the rare show that has no logo at all.
      const kind = item?.ImageTags?.Logo
        ? "Logo"
        : item?.ImageTags?.Primary
        ? "Primary"
        : "";
      if (item && kind) {
        result = {
          url: `${JELLYFIN_URL}/Items/${item.Id}/Images/${kind}?api_key=${JELLYFIN_API_KEY}`,
          type: kind === "Primary" ? "poster" : "logo",
        };
      }
    }
  } catch (e) {
    console.warn(`jellyfin token lookup failed for "${title}": ${e.message}`);
  }
  tokenCache.set(title, result);
  return result;
}

// Attach a token image URL to each now/next/later slot (in place).
async function attachTokens(slots) {
  await Promise.all(
    [slots.now, slots.next, slots.later].map(async (slot) => {
      const t = await resolveToken(slot.title);
      slot.token = t.url;
      slot.tokenType = t.type; // "poster" | "logo" | ""
    })
  );
  return slots;
}

function pickMovieCard(items) {
  if (!items.length) return null;
  const it = items[nextIndex(items)]; // the upcoming feature
  return {
    title: it.title,
    year: it.year || "",
    rating: it.rating || "",
    runtime: fmtRuntime(it.runtime),
    specs: it.specs || [],
  };
}

// ---- Remotion rendering --------------------------------------------------- //
let serveUrl = null;
async function getServeUrl() {
  if (!serveUrl) {
    console.log("bundling Remotion project (first run only)...");
    serveUrl = await bundle({ entryPoint: path.join(__dirname, "src", "index.ts") });
  }
  return serveUrl;
}

// One browser for the whole process. renderMedia() otherwise launches (and
// tears down) Chromium per call, which on a 5-card cycle meant 5 cold starts an
// hour for no reason.
let browserInstance = null;
async function getBrowser() {
  if (!browserInstance) {
    browserInstance = await openBrowser("chrome", {
      chromiumOptions: CHROMIUM_OPTIONS,
    });
    console.log(`chromium up (gl=${RENDER_GL})`);
  }
  return browserInstance;
}

const execFileAsync = promisify(execFile);
// loudnorm's JSON is NOT the last thing on stderr — ffmpeg prints the
// "[out#0/null ...]" and "frame=" summary after it — so this can't be anchored
// to the end of the output. Scan for the last flat {...} that actually parses.
const LOUDNORM_JSON_RE = /\{[^{}]*\}/g;

function parseLoudnorm(stderr) {
  const blobs = stderr.match(LOUDNORM_JSON_RE) || [];
  for (let i = blobs.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(blobs[i]);
      if ("input_i" in parsed) return parsed;
    } catch {
      // not the block we're after — keep scanning backwards
    }
  }
  return null;
}

// ffmpeg reports on stderr and still exits 0; execFile only rejects on a
// non-zero exit, so hand stderr back to the caller rather than treating it
// as failure.
async function ffmpeg(args) {
  const { stderr } = await execFileAsync("ffmpeg", args, {
    maxBuffer: 32 * 1024 * 1024,
  });
  return stderr;
}

/**
 * Two-pass loudnorm: pass 1 measures, pass 2 applies an exact linear gain.
 * Single-pass loudnorm is an adaptive filter that estimates as it goes — it
 * overshoots its own target and compresses on the way, which is exactly the
 * "why is the bumper still louder" trap. Video is stream-copied, so this costs
 * an audio re-encode (~1s), not a re-render.
 *
 * Returns true if outPath was written; false means the caller should ship
 * inPath unchanged. A loudness problem must never cost us the card.
 */
async function normalizeLoudness(inPath, outPath) {
  const base = `loudnorm=I=${LOUDNORM_I}:TP=${LOUDNORM_TP}:LRA=${LOUDNORM_LRA}`;

  let measured = null;
  try {
    const stderr = await ffmpeg([
      "-hide_banner", "-nostats", "-i", inPath,
      "-af", `${base}:print_format=json`, "-f", "null", "-",
    ]);
    measured = parseLoudnorm(stderr);
  } catch (err) {
    console.warn(`  loudness: measure failed (${err.message}) — audio as-is`);
    return false;
  }
  if (!measured) {
    console.warn("  loudness: no measurement in ffmpeg output — audio as-is");
    return false;
  }

  // parseFloat("-inf") is -Infinity, so digital silence lands here too.
  const inputI = parseFloat(measured.input_i);
  if (!Number.isFinite(inputI) || inputI < LOUDNORM_FLOOR) {
    console.log(`  loudness: silent (${measured.input_i} LUFS) — audio untouched`);
    return false;
  }

  const filter =
    `${base}:measured_I=${measured.input_i}:measured_TP=${measured.input_tp}` +
    `:measured_LRA=${measured.input_lra}:measured_thresh=${measured.input_thresh}` +
    `:offset=${measured.target_offset}:linear=true`;

  try {
    await ffmpeg([
      "-y", "-hide_banner", "-nostats", "-loglevel", "error",
      "-i", inPath, "-af", filter,
      "-c:v", "copy",
      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
      "-movflags", "+faststart", outPath,
    ]);
  } catch (err) {
    console.warn(`  loudness: apply failed (${err.message}) — audio as-is`);
    return false;
  }

  console.log(`  loudness: ${inputI.toFixed(1)} -> ${LOUDNORM_I} LUFS`);
  return true;
}

async function renderComp(url, id, inputProps, outPath) {
  const puppeteerInstance = await getBrowser();
  const t0 = Date.now();

  const composition = await selectComposition({
    serveUrl: url,
    id,
    inputProps,
    puppeteerInstance,
    chromiumOptions: CHROMIUM_OPTIONS,
  });
  const tSelect = Date.now();

  await fs.mkdir(path.dirname(outPath), { recursive: true });
  // Both staging files sit in the output dir so the final step is an atomic
  // rename and ErsatzTV never indexes a half-written card.
  const raw = `${outPath}.raw.mp4`; // Remotion's output, pre-loudness
  const tmp = `${outPath}.tmp.mp4`; // loudness-corrected, ready to publish
  await renderMedia({
    composition,
    serveUrl: url,
    codec: "h264",
    outputLocation: raw,
    inputProps,
    concurrency: CONCURRENCY,
    puppeteerInstance,
    chromiumOptions: CHROMIUM_OPTIONS,
    offthreadVideoCacheSizeInBytes: OFFTHREAD_CACHE,
    x264Preset: X264_PRESET,
  });
  const tRender = Date.now();

  let tLoud;
  try {
    // false = silent card, or ffmpeg failed; ship the render as-is either way.
    // A loudness problem must never cost us the card.
    const normalized = await normalizeLoudness(raw, tmp);
    tLoud = Date.now();
    await fs.rename(normalized ? tmp : raw, outPath);
  } finally {
    // Never leave a staging file behind. ErsatzTV indexes this folder, so an
    // orphaned .raw.mp4 would be picked up as an extra filler spot — and an
    // un-normalized one at that, reintroducing the exact problem this fixes.
    await fs.rm(raw, { force: true });
    await fs.rm(tmp, { force: true });
  }
  console.log(
    `  timing ${id}: select=${tSelect - t0}ms render=${tRender - tSelect}ms ` +
      `loudness=${tLoud - tRender}ms total=${tLoud - t0}ms ` +
      `(gl=${RENDER_GL} preset=${X264_PRESET} conc=${CONCURRENCY})`
  );
}

// Skip a re-render when the card's content is identical to what's already on
// disk — the only thing that changes between intervals is usually nothing, and
// a render is expensive. Keyed by output path; the signature captures just the
// schedule-derived content (titles/times), not the rotating b-roll/music.
const lastSig = new Map();
async function unchanged(outPath, sig) {
  if (lastSig.get(outPath) !== sig) return false;
  try {
    await fs.access(outPath);
    return true; // same content and the file is still there
  } catch {
    return false; // file went missing -> re-render
  }
}

async function renderCards() {
  const url = await getServeUrl();
  guideCache = null; // one guide fetch+parse per cycle, shared by every channel

  // now / next / later — one junction per channel, output now-next-later-<ch>.mp4
  for (const ch of CHANNELS) {
    try {
      const { channelName, items } = await getSchedule(ch);
      if (!items.length) {
        console.warn(`now/next/later: no programmes for channel "${ch}"`);
        continue;
      }
      const slots = pickNowNextLater(items);
      const outPath = path.join(NOW_DIR, `now-next-later-${ch}.mp4`);
      const sig = JSON.stringify(["nnl", channelName, slots.now, slots.next, slots.later]);
      if (await unchanged(outPath, sig)) {
        console.log(`${new Date().toISOString()}  junction [${channelName}]  line-up unchanged — skip`);
        continue;
      }
      await attachTokens(slots);
      const backgroundSrc = await pickBroll();
      const musicSrc = await pickMusic();
      await renderComp(
        url,
        "NowNextLater",
        {
          channelName,
          accent: ACCENT,
          ...slots,
          backgroundSrc,
          musicSrc,
          voiceoverSrc: VOICEOVER_SRC,
        },
        outPath
      );
      lastSig.set(outPath, sig);
      console.log(
        `${new Date().toISOString()}  junction [${channelName}]  ` +
          `NOW: ${slots.now.title} | NEXT: ${slots.next.title} | LATER: ${slots.later.title}`
      );
    } catch (e) {
      console.error(`now/next/later error [${ch}]:`, e.message);
    }
  }

  // feature presentation — one card per movie channel, output feature-<ch>.mp4
  for (const ch of MOVIE_CHANNELS) {
    try {
      const { channelName, items } = await getSchedule(ch);
      const card = pickMovieCard(items);
      if (!card) {
        console.warn(`feature: no programmes for channel "${ch}"`);
        continue;
      }
      const outPath = path.join(FEATURE_DIR, `feature-${ch}.mp4`);
      const sig = JSON.stringify(["feature", channelName, card]);
      if (await unchanged(outPath, sig)) {
        console.log(`${new Date().toISOString()}  feature [${channelName}]  ${card.title} unchanged — skip`);
        continue;
      }
      const token = (await resolveToken(card.title)).url;
      const backgroundSrc = await pickBroll();
      const musicSrc = await pickMusic();
      await renderComp(
        url,
        "FeaturePresentation",
        { channelName, accent: ACCENT, ...card, token, backgroundSrc, musicSrc },
        outPath
      );
      lastSig.set(outPath, sig);
      console.log(
        `${new Date().toISOString()}  feature [${channelName}]  ` +
          `${card.title} (${[card.rating, card.year, card.runtime].filter(Boolean).join(" · ")})`
      );
    } catch (e) {
      console.error(`feature error [${ch}]:`, e.message);
    }
  }
}

async function main() {
  console.log(
    `render-lane up. nowNextLater="${CHANNELS.join(",") || "(none)"}" ` +
      `movie="${MOVIE_CHANNELS.join(",") || "(none)"}" interval=${INTERVAL}s`
  );

  // Serve B-roll + music folders over loopback so Remotion can fetch them.
  if (!BROLL_SRC || !MUSIC_SRC) startAssetServer();

  // Warm the Remotion bundle once up front (idents are downloaded separately).
  await getServeUrl();

  for (;;) {
    await renderCards();
    await sleep(INTERVAL * 1000);
  }
}

main();
