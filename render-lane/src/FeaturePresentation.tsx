import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type FeatureProps = {
  channelName: string;
  accent: string;
  title: string;
  year: string;
  rating: string;
  runtime: string; // pre-formatted, e.g. "1h 57m"
};

const FONT = "Liberation Sans, DejaVu Sans, Arial, Helvetica, sans-serif";

// HBO-style "Feature Presentation" pre-roll. The movie's title/rating/year/
// runtime come from the ErsatzTV schedule (see generate.mjs). Cinematic
// letterbox reveal; restyle in `npx remotion studio`.
export const FeaturePresentation: React.FC<FeatureProps> = ({
  channelName,
  accent,
  title,
  year,
  rating,
  runtime,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const kicker = spring({ frame, fps, config: { damping: 200 } });
  const titleIn = spring({ frame: frame - 18, fps, config: { damping: 200 } });
  const metaIn = spring({ frame: frame - 34, fps, config: { damping: 200 } });
  const bars = interpolate(frame, [0, 20], [0, 120], { extrapolateRight: "clamp" });
  const outro = interpolate(
    frame,
    [durationInFrames - 20, durationInFrames - 1],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(80% 80% at 50% 40%, rgba(255,255,255,0.06), #000 75%)",
        }}
      />
      {/* letterbox bars sliding in */}
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: bars, background: "#000" }} />
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: bars, background: "#000" }} />

      <AbsoluteFill
        style={{
          opacity: outro,
          justifyContent: "center",
          alignItems: "center",
          textAlign: "center",
          padding: "0 140px",
        }}
      >
        <div
          style={{
            opacity: kicker,
            fontFamily: FONT,
            fontWeight: 700,
            fontSize: 34,
            letterSpacing: 14,
            color: accent,
            marginBottom: 36,
          }}
        >
          FEATURE PRESENTATION
        </div>
        <div
          style={{
            opacity: titleIn,
            transform: `translateY(${interpolate(titleIn, [0, 1], [20, 0])}px)`,
            fontFamily: FONT,
            fontWeight: 900,
            fontSize: 104,
            color: "#ffffff",
            lineHeight: 1.03,
          }}
        >
          {title}
        </div>
        <div
          style={{
            opacity: metaIn,
            marginTop: 34,
            display: "flex",
            gap: 20,
            alignItems: "center",
          }}
        >
          {rating ? (
            <span
              style={{
                fontFamily: FONT,
                fontWeight: 800,
                fontSize: 30,
                color: "#ffffff",
                border: "2px solid rgba(255,255,255,0.6)",
                borderRadius: 6,
                padding: "4px 14px",
              }}
            >
              {rating}
            </span>
          ) : null}
          <span
            style={{
              fontFamily: FONT,
              fontWeight: 600,
              fontSize: 30,
              letterSpacing: 3,
              color: "#c9ccd6",
            }}
          >
            {[year, runtime].filter(Boolean).join(" · ")}
          </span>
        </div>
      </AbsoluteFill>

      <div
        style={{
          position: "absolute",
          bottom: bars + 40,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: outro * 0.8,
          fontFamily: FONT,
          fontSize: 24,
          letterSpacing: 8,
          color: "#5b6070",
        }}
      >
        {channelName.toUpperCase()}
      </div>
    </AbsoluteFill>
  );
};
