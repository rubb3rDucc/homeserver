import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  interpolate,
  interpolateColors,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { measureText } from "@remotion/layout-utils";

type Item = { title: string; time: string; token?: string };

export type NowNextLaterProps = {
  channelName: string;
  accent: string;
  now: Item;
  next: Item;
  later: Item;
  // Optional assets. When omitted, a CSS city-haze gradient stands in for the
  // B-roll so the card still looks right in Studio with no files present.
  backgroundSrc?: string; // looping B-roll behind the tokens
  musicSrc?: string; // ambient bed
  voiceoverSrc?: string; // announcer reading the line-up
};

// Liberation/DejaVu are installed in the container (headless Chromium has no
// fonts otherwise, and text would render as empty boxes).
const FONT = "Liberation Sans, DejaVu Sans, Arial, Helvetica, sans-serif";

// CN City was pillarboxed 4:3 inside the 16:9 frame.
const AR_W = 4;
const AR_H = 3;

// The card is authored at this fixed size; the root scales it to whatever
// resolution the composition runs at (e.g. 1280x720), so none of the pixel
// math below has to change when the output resolution does.
const DESIGN_W = 1920;
const DESIGN_H = 1080;

const initials = (title: string) =>
  title
    .replace(/[^A-Za-z0-9 ]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("") || "•";

// A single now/then/later row, laid out like the CN City reference:
//   [ marching dot-arrow ]  word  [ character/logo token ]
// `intensity` (0..1) is the moving spotlight — it swells as the announcer names
// this show, lighting the arrow + token, then hands off to the next row.
const Slot: React.FC<{
  label: string;
  item: Item;
  accent: string;
  delay: number;
  intensity: number;
  onset: number; // frame the arrow lands / this row is selected
}> = ({ label, item, accent, delay, intensity, onset }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Each row drops in on its own beat -> the list builds one item at a time.
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const y = interpolate(s, [0, 1], [26, 0]);

  const g = intensity;
  // Ring pulse, keyed to THIS row's onset so the glow fires exactly when the
  // arrow lands (0.8 Hz-ish sine over a 3.33 s-scaled clock; precedence fixed:
  // divide by 3.33*fps). One positive hump (~62 frames) fades in -> holds ->
  // out across the row's turn.
  const local = frame - onset;
  const HALF = (3.33 * fps) / (2 * 0.8); // half period ≈ 62 frames
  const v = Math.min(1, Math.max(0, 1.5 * Math.sin((2 * Math.PI * 0.8 * local) / (3.33 * fps))));
  // A single hump on arrival, then dark — every row (including the last) fades
  // its ring out the same way.
  const ringGlow = local >= 0 && local <= HALF ? v : 0;
  // The glow ring is a separate overlay element (see below), so nothing here
  // touches the disc's border — that avoids the rim artifacts.
  const labelCol = interpolateColors(g, [0, 1], ["#dfe4ee", "#ffffff"]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: 28,
        opacity: s,
        transform: `translateY(${y}px)`,
      }}
    >
      {/* word + show title, right-aligned so they read into the token */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          textAlign: "right",
          minWidth: 0,
          maxWidth: 440,
        }}
      >
        <span
          style={{
            fontFamily: FONT,
            fontWeight: 700,
            fontSize: 58 + g * 12,
            color: labelCol,
            textShadow: `0 0 ${ringGlow * 26}px rgba(255,255,255,${ringGlow})`,
            lineHeight: 1,
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontFamily: FONT,
            fontWeight: 600,
            fontSize: 24 + g * 6,
            color: "#e7ebf2",
            marginTop: 8,
            textShadow: `0 0 ${ringGlow * 16}px rgba(255,255,255,${ringGlow})`,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 440,
          }}
        >
          {item.title}
          {/* {item.time ? (
            <span style={{ color: accent, fontWeight: 700 }}> · {item.time}</span>
          ) : null} */}
        </span>
      </div>

      {/* character / show-logo token. The glow ring is a SEPARATE overlay
          element (below) so it can pulse independently and never draws onto the
          disc's own border — which is what caused the rim artifacts. */}
      <div
        style={{
          position: "relative",
          width: 176,
          height: 176,
          flexShrink: 0,
          transform: `scale(${1 + g * 0.05})`,
        }}
      >
        {/* independent white glow halo — its own circle just outside the disc */}
        <div
          style={{
            position: "absolute",
            inset: -8,
            borderRadius: "50%",
            border: `3px solid rgba(255,255,255,${ringGlow * 0.9})`,
            boxShadow: `0 0 ${ringGlow * 40}px ${ringGlow * 12}px rgba(255,255,255,0.85)`,
            opacity: ringGlow,
            pointerEvents: "none",
          }}
        />
        {/* clean disc + logo (neutral rim, no accent border) */}
        <div
          style={{
            width: "100%",
            height: "100%",
            borderRadius: "50%",
            overflow: "hidden",
            background:
              "radial-gradient(circle at 35% 30%, #fdfdf5, #e9e7d6 70%, #cfcbb4)",
            border: "3px solid rgba(255,255,255,0.85)",
            boxShadow: "0 3px 16px rgba(0,0,0,0.45)",
            filter: `brightness(${1 + g * 0.12})`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: FONT,
            fontWeight: 800,
            fontSize: 60,
            color: "#3a3a44",
            letterSpacing: 1,
          }}
        >
          {item.token ? (
            // contain + padding so wide show logos sit inside the disc uncropped
            <img
              src={item.token}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
                padding: 20,
                boxSizing: "border-box",
              }}
            />
          ) : (
            initials(item.title)
          )}
        </div>
      </div>
    </div>
    );
  };

// The single white marching dot-arrow that travels down the list. Built like
// the reference: a continuous horizontal shaft of dots running through to the
// tip, with two wings fanning up-left and down-left from the tip.
const ARROW_COLOR = "#ffffff";
const DOT_GLOW = "0 0 10px rgba(255,255,255,0.9)";
const DOT = 17;
const STEP = 20; // spacing between dot centers
// Dot centers: x = distance from the left, y = offset from the vertical center.
const SHAFT = [0, 1, 2, 3, 4, 5, 6].map((i) => ({ x: i * STEP, k: i })); // -> tip
const WINGS = [
  { x: 5 * STEP, y: -STEP }, // upper inner
  { x: 4 * STEP, y: -2 * STEP }, // upper outer
  { x: 5 * STEP, y: STEP }, // lower inner
  { x: 4 * STEP, y: 2 * STEP }, // lower outer
];
const ARROW_W = 6 * STEP + DOT; // tip at x = 6*STEP
const ARROW_H = 4 * STEP + DOT;
const DotArrow: React.FC<{ opacity: number }> = ({ opacity }) => {
  const frame = useCurrentFrame();
  const cy = ARROW_H / 2;
  const dot = (x: number, y: number, o: number, key: string) => (
    <div
      key={key}
      style={{
        position: "absolute",
        left: x,
        top: cy + y - DOT / 2,
        width: DOT,
        height: DOT,
        borderRadius: "50%",
        background: ARROW_COLOR,
        boxShadow: DOT_GLOW,
        opacity: o,
      }}
    />
  );
  // Marching highlight sweeping left -> right by dot column (x / STEP). Wings key
  // off their own column too, so the illumination flows through them, not just
  // the shaft.
  const march = (col: number) => {
    const phase = (frame / 4 - col) % (SHAFT.length + 2);
    return phase >= 0 && phase < 2 ? 1 : 0.4;
  };
  return (
    <div style={{ position: "relative", width: ARROW_W, height: ARROW_H, opacity }}>
      {SHAFT.map((d) => dot(d.x, 0, march(d.k), `s${d.k}`))}
      {WINGS.map((d, i) => dot(d.x, d.y, march(d.x / STEP), `w${i}`))}
    </div>
  );
};

export const NowNextLater: React.FC<NowNextLaterProps> = ({
  channelName,
  accent,
  now,
  next,
  later,
  backgroundSrc,
  musicSrc,
  voiceoverSrc,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();

  // Ending: the now/next/later slots fade out and the channel bug is revealed,
  // then held alone (over the background) for a beat before the clip ends.
  const slotsOut = interpolate(
    frame,
    [durationInFrames - 100, durationInFrames - 75],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const bugIn = interpolate(
    frame,
    [durationInFrames - 85, durationInFrames - 60],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  // Per-row onset = the frame the arrow lands / the row is selected. Everything
  // (arrow, glow, spotlight) keys off this so they stay in lockstep.
  const HOLD_START = 46;
  const PER_SLOT = 64;
  const onset = (i: number) => HOLD_START + i * PER_SLOT; // 46 / 110 / 174
  const spotlight = (i: number) => {
    const a = onset(i);
    return interpolate(
      frame,
      [a - 12, a + 8, a + PER_SLOT - 8, a + PER_SLOT + 12],
      [0, 1, 1, 0],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );
  };
  // Each row drops in just before its onset, so the list builds one at a time.
  const slotIn = (i: number) => onset(i) - 8;

  // Single arrow travelling down the list: it dwells on a row while that show is
  // presented, then a quick slide lands it on the next row exactly at its onset
  // (so the arrow arrival and the ring glow fire together). ROW_Y are the token
  // centers from the stage top; tune if spacing changes.
  const ROW_Y = [204, 540, 876];
  const MOVE = 7; // frames to slide between rows
  const arrowY = interpolate(
    frame,
    [onset(0), onset(1) - MOVE, onset(1), onset(2) - MOVE, onset(2)],
    [ROW_Y[0], ROW_Y[0], ROW_Y[1], ROW_Y[1], ROW_Y[2]],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  // The arrow sits just left of the active row's text, so it slides HORIZONTALLY
  // too — long titles no longer end up underneath it. Widths are measured at the
  // selected (grown) font sizes and capped at the text block's maxWidth (440).
  const textW = (label: string, title: string) =>
    Math.min(
      440,
      Math.max(
        measureText({ text: label, fontFamily: FONT, fontSize: 70, fontWeight: 700 }).width,
        measureText({ text: title, fontFamily: FONT, fontSize: 30, fontWeight: 600 }).width
      )
    );
  const TEXT_RIGHT = 274; // right edge of the text block, from the stage right
  const aR = [
    textW("now", now.title),
    textW("next", next.title),
    textW("later", later.title),
  ].map((w) => TEXT_RIGHT + w + 26); // arrow tip = text left edge + gap
  const arrowRight = interpolate(
    frame,
    [onset(0), onset(1) - MOVE, onset(1), onset(2) - MOVE, onset(2)],
    [aR[0], aR[0], aR[1], aR[1], aR[2]],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const arrowOpacity = interpolate(
    frame,
    [HOLD_START - 10, HOLD_START + 6],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  // 4:3 pillarbox centered in the 16:9 design canvas.
  const boxH = DESIGN_H;
  const boxW = (DESIGN_H * AR_W) / AR_H;
  const boxLeft = (DESIGN_W - boxW) / 2;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* authored at 1920x1080; scale the whole canvas to the composition's
          actual resolution (e.g. 1280x720) so the render is lighter */}
      <AbsoluteFill
        style={{
          width: DESIGN_W,
          height: DESIGN_H,
          transformOrigin: "top left",
          transform: `scale(${width / DESIGN_W})`,
        }}
      >
      {/* --- audio beds --- */}
      {musicSrc ? (
        <Audio
          src={musicSrc}
          volume={(f) =>
            interpolate(
              f,
              [0, 15, durationInFrames - 20, durationInFrames - 1],
              [0, 0.6, 0.6, 0],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            )
          }
        />
      ) : null}
      {voiceoverSrc ? <Audio src={voiceoverSrc} startFrom={0} /> : null}

      {/* --- pillarboxed 4:3 stage --- */}
      <div
        style={{
          position: "absolute",
          left: boxLeft,
          top: 0,
          width: boxW,
          height: boxH,
          overflow: "hidden",
        }}
      >
        {backgroundSrc ? (
          <OffthreadVideo
            src={backgroundSrc}
            muted
            loop
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              filter: "saturate(0.7) brightness(0.95)",
            }}
          />
        ) : (
          // City-haze stand-in: pale daylight blue washing down to a skyline grey.
          <AbsoluteFill
            style={{
              background:
                "linear-gradient(180deg, #aebccd 0%, #97a6bb 45%, #7f8ea6 78%, #6f7d95 100%)",
            }}
          />
        )}
        {/* muted-blue haze for the era's desaturated daylight */}
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(180deg, rgba(120,140,170,0.28), rgba(120,140,170,0.10) 40%, rgba(120,140,170,0.0))",
          }}
        />
        {/* right-side scrim covering the full-height now/next/later column, so
            the white type stays legible over bright B-roll. Bump the last stop
            (0.7) toward 1 if your footage is brighter still. */}
        <AbsoluteFill
          style={{
            background:
              "linear-gradient(90deg, rgba(15,20,34,0) 20%, rgba(15,20,34,0.42) 52%, rgba(15,20,34,0.72) 100%)",
          }}
        />

        {/* --- now / then / later group: compact, top-right corner --- */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            opacity: slotsOut,
            padding: "116px 70px 116px 0",
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            justifyContent: "space-between",
          }}
        >
          {/* one white arrow, gliding down to the row being presented */}
          <div
            style={{
              position: "absolute",
              top: arrowY,
              right: arrowRight,
              transform: "translateY(-50%)",
              opacity: arrowOpacity,
            }}
          >
            <DotArrow opacity={1} />
          </div>
          <Slot label="now" item={now} accent={accent} delay={slotIn(0)} intensity={spotlight(0)} onset={onset(0)} />
          <Slot label="next" item={next} accent={accent} delay={slotIn(1)} intensity={spotlight(1)} onset={onset(1)} />
          <Slot label="later" item={later} accent={accent} delay={slotIn(2)} intensity={spotlight(2)} onset={onset(2)} />
        </div>

        {/* channel bug — revealed as the slots fade out, held alone at the end */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            opacity: bugIn,
            pointerEvents: "none",
          }}
        >
          <span
            style={{
              fontFamily: FONT,
              fontSize: 72,
              fontWeight: 800,
              letterSpacing: 8,
              color: "#f4f6fb",
              textShadow: "0 0 34px rgba(255,255,255,0.55)",
            }}
          >
            {channelName.toUpperCase()}
          </span>
        </div>
      </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
