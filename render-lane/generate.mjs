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
import { selectComposition, renderMedia } from "@remotion/renderer";
import { XMLParser } from "fast-xml-parser";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import http from "node:http";
import { createReadStream } from "node:fs";

const ERSATZTV_URL = process.env.ERSATZTV_URL || "http://ersatztv:8409";
const CHANNEL = process.env.CHANNEL || ""; // channel for now/next/later ("" = skip)
// One or more channels for the feature card (comma-separated) -> one card each.
const MOVIE_CHANNELS = (process.env.MOVIE_CHANNEL || CHANNEL || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const INTERVAL = parseInt(process.env.INTERVAL || "300", 10);
const ACCENT = process.env.ACCENT || "#e50914";

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

const OUTPUT = process.env.OUTPUT || "/station/library/junctions/now-next-later.mp4";
const FEATURE_DIR = process.env.FEATURE_DIR || "/station/library/feature";
// Render the feature card at a fraction of 1080p to save CPU (0.6667 -> 720p).
const FEATURE_SCALE = parseFloat(process.env.FEATURE_SCALE || "0.6667");
const IDENT_OUTPUT = process.env.IDENT_OUTPUT || "/station/library/idents/ident.mp4";

const IDENT_NAME = process.env.IDENT_NAME || "My Channel";
const IDENT_TAGLINE = process.env.IDENT_TAGLINE || "";

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

async function getSchedule(channel) {
  const res = await fetch(`${ERSATZTV_URL}/iptv/xmltv.xml`);
  if (!res.ok) throw new Error(`XMLTV fetch failed: HTTP ${res.status}`);
  const xml = await res.text();

  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "@_",
  });
  const tv = parser.parse(xml).tv || {};

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
// Match a programme title to a Jellyfin item and return its Logo (else Primary)
// image URL for the round token. Results are cached by title (art is stable).
const tokenCache = new Map();
async function resolveToken(title) {
  if (!JELLYFIN_API_KEY || !title || title === "—") return "";
  if (tokenCache.has(title)) return tokenCache.get(title);

  let url = "";
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
      const type = item?.ImageTags?.Logo
        ? "Logo"
        : item?.ImageTags?.Primary
        ? "Primary"
        : "";
      if (item && type) {
        url = `${JELLYFIN_URL}/Items/${item.Id}/Images/${type}?api_key=${JELLYFIN_API_KEY}`;
      }
    }
  } catch (e) {
    console.warn(`jellyfin token lookup failed for "${title}": ${e.message}`);
  }
  tokenCache.set(title, url);
  return url;
}

// Attach a token image URL to each now/next/later slot (in place).
async function attachTokens(slots) {
  await Promise.all(
    [slots.now, slots.next, slots.later].map(async (slot) => {
      slot.token = await resolveToken(slot.title);
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

async function renderComp(url, id, inputProps, outPath, opts = {}) {
  const composition = await selectComposition({ serveUrl: url, id, inputProps });
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  const tmp = `${outPath}.tmp.mp4`; // same dir -> atomic rename, never serve a half file
  await renderMedia({
    composition,
    serveUrl: url,
    codec: "h264",
    outputLocation: tmp,
    inputProps,
    scale: opts.scale, // <1 renders at lower resolution (cheaper), same layout
  });
  await fs.rename(tmp, outPath);
}

async function renderCards() {
  const url = await getServeUrl();

  // now / next / later (skipped while CHANNEL is blank)
  if (CHANNEL) {
    try {
      const { channelName, items } = await getSchedule(CHANNEL);
      if (items.length) {
        const slots = await attachTokens(pickNowNextLater(items));
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
          OUTPUT
        );
        console.log(
          `${new Date().toISOString()}  junction [${channelName}]  ` +
            `NOW: ${slots.now.title} | NEXT: ${slots.next.title} | LATER: ${slots.later.title}`
        );
      } else {
        console.warn(`now/next/later: no programmes for channel "${CHANNEL}"`);
      }
    } catch (e) {
      console.error("now/next/later error:", e.message);
    }
  }

  // feature presentation — one card per movie channel, output feature-<ch>.mp4
  for (const ch of MOVIE_CHANNELS) {
    try {
      const { channelName, items } = await getSchedule(ch);
      const card = pickMovieCard(items);
      if (card) {
        const token = await resolveToken(card.title);
        const backgroundSrc = await pickBroll();
        const musicSrc = await pickMusic();
        await renderComp(
          url,
          "FeaturePresentation",
          { channelName, accent: ACCENT, ...card, token, backgroundSrc, musicSrc },
          path.join(FEATURE_DIR, `feature-${ch}.mp4`),
          { scale: FEATURE_SCALE }
        );
        console.log(
          `${new Date().toISOString()}  feature [${channelName}]  ` +
            `${card.title} (${[card.rating, card.year, card.runtime].filter(Boolean).join(" · ")})`
        );
      } else {
        console.warn(`feature: no programmes for channel "${ch}"`);
      }
    } catch (e) {
      console.error(`feature error [${ch}]:`, e.message);
    }
  }
}

async function main() {
  console.log(
    `render-lane up. channel="${CHANNEL || "(skipped)"}" movieChannels="${MOVIE_CHANNELS.join(",")}" interval=${INTERVAL}s`
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
