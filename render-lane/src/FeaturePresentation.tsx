import React from "react";
import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT } from "./theme";

export type FeatureSpec = { code: string; lines: string[] };

export type FeatureProps = {
  channelName: string;
  accent: string;
  title: string;
  year: string;
  rating: string;
  runtime: string; // pre-formatted, e.g. "1h 57m"
  specs?: FeatureSpec[]; // format/caption/language rows, filled per-program
  token?: string; // unused here (kept for prop compatibility)
  backgroundSrc?: string; // unused here (Feature has its own ambient bg)
  musicSrc?: string; // ambient bed
};

// Fallback spec rows (the classic HBO trio) when the schedule gives us nothing.
const DEFAULT_SPECS: FeatureSpec[] = [
  { code: "dolby", lines: ["DOLBY DIGITAL 5.1 SOUND", "WHERE AVAILABLE"] },
  { code: "cc", lines: ["CLOSED CAPTIONED"] },
  { code: "esp", lines: ["EN ESPAÑOL"] },
];

const iconFor = (code: string): React.ReactNode => {
  if (code === "dolby") return <Dolby />;
  if (code === "cc") return "CC";
  return <span style={{ fontSize: 20 }}>{code.toUpperCase().slice(0, 3)}</span>;
};

// Soft organic cloud blobs for the background: a saturated multi-color wash
// (orange / amber / magenta / purple / blue / teal) plus white cores. cx,cy are
// the blob center in %, w,h its size in %, `br` an irregular border-radius so it
// reads as a cloud (not a circle), and spd/amp/ph drive the drift + morph. The
// top band hugs the bar's top edge (~27%) and the bottom band its bottom edge
// (~73%); nothing sits at cy~50 so the middle stays dark behind the text.
type Cloud = {
  color: string;
  cx: number;
  cy: number;
  w: number;
  h: number;
  br: string;
  spd: number;
  amp: number;
  ph: number;
};
// Saturated complementary pairs: orange<->blue, magenta<->green, purple<->gold,
// red<->cyan. Warm hues live up top, their cool complements below.
const CLOUDS: Cloud[] = [
  { color: "rgba(255,85,0,0.95)", cx: 20, cy: 16, w: 42, h: 24, br: "46% 54% 60% 40% / 54% 46% 54% 46%", spd: 46, amp: 9, ph: 0 },
  { color: "rgba(255,180,0,0.92)", cx: 40, cy: 11, w: 30, h: 20, br: "58% 42% 48% 52% / 44% 56% 44% 56%", spd: 60, amp: 7, ph: 1.2 },
  { color: "rgba(255,0,150,0.9)", cx: 60, cy: 18, w: 34, h: 22, br: "40% 60% 55% 45% / 60% 40% 55% 45%", spd: 52, amp: 8, ph: 2.1 },
  { color: "rgba(0,110,255,0.95)", cx: 83, cy: 13, w: 36, h: 24, br: "55% 45% 42% 58% / 48% 55% 45% 52%", spd: 54, amp: 8, ph: 3.0 },
  { color: "rgba(255,255,255,0.6)", cx: 33, cy: 21, w: 13, h: 14, br: "50%", spd: 46, amp: 9, ph: 0.4 },
  { color: "rgba(150,0,255,0.9)", cx: 24, cy: 83, w: 36, h: 22, br: "60% 40% 50% 50% / 45% 55% 50% 50%", spd: 64, amp: 9, ph: 3.4 },
  { color: "rgba(255,30,40,0.92)", cx: 47, cy: 89, w: 40, h: 22, br: "44% 56% 58% 42% / 56% 44% 52% 48%", spd: 58, amp: 10, ph: 1.7 },
  { color: "rgba(0,220,140,0.9)", cx: 67, cy: 85, w: 32, h: 20, br: "52% 48% 45% 55% / 50% 50% 45% 55%", spd: 50, amp: 8, ph: 2.6 },
  { color: "rgba(0,210,255,0.9)", cx: 85, cy: 87, w: 36, h: 22, br: "46% 54% 55% 45% / 55% 45% 55% 45%", spd: 56, amp: 8, ph: 0.9 },
  { color: "rgba(255,255,255,0.5)", cx: 70, cy: 80, w: 12, h: 12, br: "50%", spd: 50, amp: 7, ph: 1.1 },
];

// Classic HBO "Feature Presentation" spec bumper: warm/cool ambient mist top and
// bottom, a light "THE FOLLOWING MOVIE IS RATED / R" card on the left, and the
// Dolby / Closed-Captioned / En Español spec rows on the right.

// The Dolby back-to-back double-D mark.
const Dolby: React.FC = () => (
  <svg width="46" height="34" viewBox="0 0 92 68">
    <path d="M44 6 H26 A28 28 0 0 0 26 62 H44 Z" fill="#ffffff" />
    <path d="M48 6 H66 A28 28 0 0 1 66 62 H48 Z" fill="#ffffff" />
  </svg>
);

const IconBox: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      width: 66,
      height: 66,
      flexShrink: 0,
      background: "transparent",
      border: "3px solid #ffffff",
      borderRadius: 8,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: FONT,
      fontWeight: 800,
      fontSize: 26,
      letterSpacing: 1,
      color: "#ffffff",
    }}
  >
    {children}
  </div>
);

const SpecRow: React.FC<{
  icon: React.ReactNode;
  lines: string[];
  o: number;
  dy: number;
}> = ({ icon, lines, o, dy }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 26,
      opacity: o,
      transform: `translateX(${dy}px)`,
    }}
  >
    <IconBox>{icon}</IconBox>
    <div style={{ display: "flex", flexDirection: "column" }}>
      {lines.map((l, i) => (
        <span
          key={i}
          style={{
            fontFamily: FONT,
            fontWeight: 800,
            fontSize: i === 0 ? 40 : 30,
            letterSpacing: 1,
            color: i === 0 ? "#ffffff" : "#c9ccd2",
            lineHeight: 1.12,
            textTransform: "lowercase",
          }}
        >
          {l}
        </span>
      ))}
    </div>
  </div>
);

export const FeaturePresentation: React.FC<FeatureProps> = ({
  channelName,
  title,
  year,
  rating,
  runtime,
  specs,
  musicSrc,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width } = useVideoConfig();

  const cardIn = spring({ frame, fps, config: { damping: 200 } });
  const row = (d: number) => spring({ frame: frame - d, fps, config: { damping: 200 } });
  // Ending: hold the fully-assembled card for a beat, then a hard cut to black
  // with a short black tail before the clip ends.
  const CUT = durationInFrames - 18;
  const visible = frame < CUT ? 1 : 0;

  const ratingText = (rating || "NR").toUpperCase();
  const ratingSize = ratingText.length <= 2 ? 132 : ratingText.length <= 4 ? 84 : 56;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* the card is designed at 1920x1080; scale the whole canvas to fill
          whatever resolution the composition is (e.g. 1280x720) */}
      <AbsoluteFill
        style={{
          width: 1920,
          height: 1080,
          transformOrigin: "top left",
          transform: `scale(${width / 1920})`,
        }}
      >
      {musicSrc ? (
        <Audio
          src={musicSrc}
          volume={(f) =>
            interpolate(
              f,
              [0, 12, durationInFrames - 12, durationInFrames - 1],
              [0, 0.55, 0.55, 0],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            )
          }
        />
      ) : null}

      {/* "goo" metaball filter: blur then threshold alpha so overlapping blobs
          fuse into one another as they drift — the lava-lamp merge. */}
      <svg style={{ position: "absolute", width: 0, height: 0 }} aria-hidden>
        <defs>
          <filter id="goo" x="-25%" y="-25%" width="150%" height="150%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="34" result="blur" />
            <feColorMatrix
              in="blur"
              mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 20 -9"
            />
          </filter>
        </defs>
      </svg>

      {/* lava-lamp blobs: slow drift + gooey merge, saturated multi-color wash
          hugging the black bar's top/bottom edges */}
      <AbsoluteFill style={{ filter: "url(#goo) blur(58px)" }}>
        {CLOUDS.map((b, i) => {
          // slow — long periods so the blobs ooze rather than dart
          const dx = b.amp * 1.7 * Math.sin(frame / (b.spd * 2.4) + b.ph);
          const dy = b.amp * 0.8 * Math.cos(frame / (b.spd * 3.0) + b.ph);
          const pulse = 1 + 0.13 * Math.sin(frame / (b.spd * 2.0) + b.ph);
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: `${b.cx + dx - b.w / 2}%`,
                top: `${b.cy + dy - b.h / 2}%`,
                width: `${b.w}%`,
                height: `${b.h}%`,
                background: b.color,
                borderRadius: b.br,
                transform: `scale(${pulse})`,
              }}
            />
          );
        })}
      </AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "27%",
          height: "46%",
          // semi-transparent so the mist glow reads through the whole band and,
          // with the transparent assets, "shines through" the shapes
          background: "rgba(0,0,0,0.4)",
        }}
      />

      {/* content band */}
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          gap: 80,
        }}
      >
        {/* rating card — transparent so the mist glow reads through it */}
        <div
          style={{
            position: "relative",
            width: 340,
            opacity: cardIn,
            transform: `scale(${interpolate(cardIn, [0, 1], [0.92, 1])})`,
            background: "transparent",
            color: "#ffffff",
            border: "2px solid rgba(255,255,255,0.85)",
          }}
        >
          <div
            style={{
              padding: "26px 20px 22px",
              textAlign: "center",
              fontFamily: FONT,
              fontWeight: 700,
              fontSize: 26,
              letterSpacing: 2,
              lineHeight: 1.15,
              textTransform: "lowercase",
              borderBottom: "2px solid rgba(255,255,255,0.85)",
            }}
          >
            the following
            <br />
            film is rated
          </div>
          <div
            style={{
              padding: "10px 0",
              textAlign: "center",
              fontFamily: FONT,
              fontWeight: 800,
              fontSize: ratingSize,
              lineHeight: 1.1,
              borderBottom: "2px solid rgba(255,255,255,0.85)",
            }}
          >
            {ratingText}
          </div>
          {/* <div style={{ padding: "16px 18px 26px", textAlign: "center" }}> */}
          {/* <div
              style={{
                fontFamily: FONT,
                fontWeight: 800,
                fontSize: 24,
                lineHeight: 1.12,
                color: "#ffffff",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {title}
            </div> */}
          {/* {year || runtime ? (
              <div
                style={{
                  fontFamily: FONT,
                  fontWeight: 600,
                  fontSize: 16,
                  letterSpacing: 1,
                  color: "rgba(255,255,255,0.75)",
                  marginTop: 6,
                }}
              >
                {[year, runtime].filter(Boolean).join("  ·  ")}
              </div>
            ) : null} */}
          {/* </div> */}
        </div>

        {/* spec rows — filled per-program, else the classic HBO trio */}
        <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
          {(specs && specs.length ? specs : DEFAULT_SPECS).map((s, i) => {
            const a = row(10 + i * 10);
            return (
              <SpecRow
                key={i}
                icon={iconFor(s.code)}
                lines={s.lines}
                o={a}
                dy={interpolate(a, [0, 1], [24, 0])}
              />
            );
          })}
        </div>
      </AbsoluteFill>

      {/* channel bug */}
      <div
        style={{
          position: "absolute",
          bottom: 40,
          left: 0,
          right: 0,
          textAlign: "center",
          fontFamily: FONT,
          fontSize: 22,
          letterSpacing: 4,
          fontWeight: 600,
          color: "#8a8f9a",
          textTransform: "lowercase",
        }}
      >
        {channelName}
      </div>
      </AbsoluteFill>

      {/* hard cut to black after the hold */}
      {!visible ? <AbsoluteFill style={{ backgroundColor: "#000" }} /> : null}
    </AbsoluteFill>
  );
};
