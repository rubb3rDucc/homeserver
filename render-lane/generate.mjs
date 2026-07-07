/**
 * Render lane orchestrator.
 *
 * On an interval: fetch the ErsatzTV XMLTV guide, work out what's on now / next
 * / later for the configured channel, and render a junction card MP4 with
 * Remotion into the station library. ErsatzTV then plays that card as filler.
 *
 * The XMLTV guide is used (rather than a private API) because it's a stable,
 * documented ErsatzTV endpoint that already contains every programme's
 * start/stop time and title.
 */
import { bundle } from "@remotion/bundler";
import { selectComposition, renderMedia } from "@remotion/renderer";
import { XMLParser } from "fast-xml-parser";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";

const ERSATZTV_URL = process.env.ERSATZTV_URL || "http://ersatztv:8409";
const CHANNEL = process.env.CHANNEL || "1"; // channel number or display-name
const INTERVAL = parseInt(process.env.INTERVAL || "300", 10);
const ACCENT = process.env.ACCENT || "#e50914";
const OUTPUT =
  process.env.OUTPUT || "/station/library/junctions/now-next-later.mp4";
const COMPOSITION_ID = "NowNextLater";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ---- XMLTV helpers -------------------------------------------------------- //
function parseXmltvTime(s) {
  // e.g. "20260707183000 +0000"
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

async function getSchedule() {
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
    return id === CHANNEL || names.includes(CHANNEL);
  });
  const channelId = match ? String(match["@_id"]) : CHANNEL;
  const channelName = match
    ? String(text(arr(match["display-name"])[0]) ?? CHANNEL)
    : CHANNEL;

  const items = arr(tv.programme)
    .filter((p) => String(p["@_channel"]) === channelId)
    .map((p) => ({
      title: String(text(p.title) ?? "Untitled"),
      start: parseXmltvTime(p["@_start"]),
      stop: parseXmltvTime(p["@_stop"]),
    }))
    .filter((p) => p.start && p.stop)
    .sort((a, b) => a.start - b.start);

  return { channelName, items };
}

function pickNowNextLater(items) {
  const now = new Date();
  let idx = items.findIndex((p) => p.start <= now && now < p.stop);
  if (idx === -1) {
    // Nothing airing right now (gap) -> treat the next upcoming item as "now".
    idx = items.findIndex((p) => p.start > now);
    if (idx === -1) idx = items.length - 1;
  }
  const fmt = (d) =>
    d ? d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
  const at = (i) =>
    items[i]
      ? { title: items[i].title, time: fmt(items[i].start) }
      : { title: "—", time: "" };
  return { now: at(idx), next: at(idx + 1), later: at(idx + 2) };
}

// ---- Remotion rendering --------------------------------------------------- //
let serveUrl = null;
async function getServeUrl() {
  if (!serveUrl) {
    console.log("bundling Remotion project (first run only)...");
    serveUrl = await bundle({
      entryPoint: path.join(__dirname, "src", "index.ts"),
    });
  }
  return serveUrl;
}

async function renderOnce() {
  const { channelName, items } = await getSchedule();
  if (!items.length) {
    console.warn(`no programmes found for channel "${CHANNEL}" - skipping`);
    return;
  }
  const slots = pickNowNextLater(items);
  const inputProps = { channelName, accent: ACCENT, ...slots };

  const url = await getServeUrl();
  const composition = await selectComposition({
    serveUrl: url,
    id: COMPOSITION_ID,
    inputProps,
  });

  await fs.mkdir(path.dirname(OUTPUT), { recursive: true });
  const tmp = `${OUTPUT}.tmp.mp4`; // same dir -> atomic rename, no half-written file on air
  await renderMedia({
    composition,
    serveUrl: url,
    codec: "h264",
    outputLocation: tmp,
    inputProps,
  });
  await fs.rename(tmp, OUTPUT);

  console.log(
    `${new Date().toISOString()}  rendered [${channelName}]  ` +
      `NOW: ${slots.now.title} | NEXT: ${slots.next.title} | LATER: ${slots.later.title}`
  );
}

async function main() {
  console.log(
    `render-lane up. channel="${CHANNEL}" interval=${INTERVAL}s output=${OUTPUT}`
  );
  for (;;) {
    try {
      await renderOnce();
    } catch (e) {
      console.error("render error:", e.message);
    }
    await new Promise((r) => setTimeout(r, INTERVAL * 1000));
  }
}

main();
