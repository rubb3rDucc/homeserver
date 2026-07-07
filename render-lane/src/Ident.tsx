import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type IdentProps = {
  channelName: string;
  accent: string;
  tagline: string;
};

const FONT = "Liberation Sans, DejaVu Sans, Arial, Helvetica, sans-serif";

// A typographic channel ident. Restyle freely in `npx remotion studio`:
// swap the wordmark for <Img src={staticFile('logo.png')} />, change the
// sweep/scale, add <Audio> for a sound logo, etc.
export const Ident: React.FC<IdentProps> = ({ channelName, accent, tagline }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200 } });
  const scale = interpolate(enter, [0, 1], [0.82, 1]);
  const sweep = interpolate(frame, [0, 30], [-width, 0], {
    extrapolateRight: "clamp",
  });
  const outro = interpolate(
    frame,
    [durationInFrames - 18, durationInFrames - 1],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#08080c",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(60% 60% at 50% 45%, rgba(255,255,255,0.08), transparent 70%)",
        }}
      />
      <div style={{ opacity: outro, textAlign: "center", transform: `scale(${scale})` }}>
        <div
          style={{
            opacity: enter,
            fontFamily: FONT,
            fontWeight: 900,
            fontSize: 170,
            letterSpacing: -2,
            color: "#ffffff",
            lineHeight: 1,
          }}
        >
          {channelName.toUpperCase()}
        </div>
        <div
          style={{
            position: "relative",
            height: 8,
            margin: "28px auto 0",
            width: 520,
            overflow: "hidden",
            borderRadius: 4,
            background: "rgba(255,255,255,0.08)",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              transform: `translateX(${sweep}px)`,
              background: accent,
            }}
          />
        </div>
        {tagline ? (
          <div
            style={{
              opacity: enter,
              marginTop: 26,
              fontFamily: FONT,
              fontWeight: 600,
              fontSize: 34,
              letterSpacing: 6,
              color: accent,
            }}
          >
            {tagline.toUpperCase()}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
