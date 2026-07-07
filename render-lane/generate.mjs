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

const ERSATZTV_URL = process.env.ERSATZTV_URL || "http://ersatztv:8409";
const CHANNEL = process.env.CHANNEL || "1"; // channel for now/next/later
const MOVIE_CHANNEL = process.env.MOVIE_CHANNEL || CHANNEL; // channel for feature card
const INTERVAL = parseInt(process.env.INTERVAL || "300", 10);
const ACCENT = process.env.ACCENT || "#e50914";

const OUTPUT = process.env.OUTPUT || "/station/library/junctions/now-next-later.mp4";
const FEATURE_OUTPUT =
  process.env.FEATURE_OUTPUT || "/station/library/feature/feature-presentation.mp4";
const IDENT_OUTPUT = process.env.IDENT_OUTPUT || "/station/library/idents/ident.mp4";

const IDENT_NAME = process.env.IDENT_NAME || "My Channel";
const IDENT_TAGLINE = process.env.IDENT_TAGLINE || "";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

function pickMovieCard(items) {
  if (!items.length) return null;
  const it = items[currentIndex(items)];
  return {
    title: it.title,
    year: it.year || "",
    rating: it.rating || "",
    runtime: fmtRuntime(it.runtime),
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

async function renderComp(url, id, inputProps, outPath) {
  const composition = await selectComposition({ serveUrl: url, id, inputProps });
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  const tmp = `${outPath}.tmp.mp4`; // same dir -> atomic rename, never serve a half file
  await renderMedia({
    composition,
    serveUrl: url,
    codec: "h264",
    outputLocation: tmp,
    inputProps,
  });
  await fs.rename(tmp, outPath);
}

async function renderCards() {
  const url = await getServeUrl();

  // now / next / later
  try {
    const { channelName, items } = await getSchedule(CHANNEL);
    if (items.length) {
      const slots = pickNowNextLater(items);
      await renderComp(url, "NowNextLater", { channelName, accent: ACCENT, ...slots }, OUTPUT);
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

  // feature presentation (movie channel)
  try {
    const { channelName, items } = await getSchedule(MOVIE_CHANNEL);
    const card = pickMovieCard(items);
    if (card) {
      await renderComp(
        url,
        "FeaturePresentation",
        { channelName, accent: ACCENT, ...card },
        FEATURE_OUTPUT
      );
      console.log(
        `${new Date().toISOString()}  feature [${channelName}]  ` +
          `${card.title} (${[card.rating, card.year, card.runtime].filter(Boolean).join(" · ")})`
      );
    } else {
      console.warn(`feature: no programmes for channel "${MOVIE_CHANNEL}"`);
    }
  } catch (e) {
    console.error("feature error:", e.message);
  }
}

async function main() {
  console.log(
    `render-lane up. channel="${CHANNEL}" movieChannel="${MOVIE_CHANNEL}" interval=${INTERVAL}s`
  );

  // Static ident: render once at startup (no schedule needed).
  try {
    const url = await getServeUrl();
    await renderComp(
      url,
      "Ident",
      { channelName: IDENT_NAME, accent: ACCENT, tagline: IDENT_TAGLINE },
      IDENT_OUTPUT
    );
    console.log(`rendered ident -> ${IDENT_OUTPUT}`);
  } catch (e) {
    console.error("ident error:", e.message);
  }

  for (;;) {
    await renderCards();
    await sleep(INTERVAL * 1000);
  }
}

main();
