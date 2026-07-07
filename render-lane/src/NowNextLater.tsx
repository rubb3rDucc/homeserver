import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Item = { title: string; time: string };

export type NowNextLaterProps = {
  channelName: string;
  accent: string;
  now: Item;
  next: Item;
  later: Item;
};

// Liberation/DejaVu are installed in the container (headless Chromium has no
// fonts otherwise, and text would render as empty boxes).
const FONT = "Liberation Sans, DejaVu Sans, Arial, Helvetica, sans-serif";

const Slot: React.FC<{
  label: string;
  item: Item;
  accent: string;
  delay: number;
  emphasis?: boolean;
}> = ({ label, item, accent, delay, emphasis }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const x = interpolate(s, [0, 1], [-60, 0]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 32,
        opacity: s,
        transform: `translateX(${x}px)`,
        marginBottom: emphasis ? 40 : 28,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontWeight: 800,
          fontSize: 32,
          letterSpacing: 3,
          color: emphasis ? "#0b0b0f" : "#cfd2dc",
          background: emphasis ? accent : "transparent",
          border: emphasis ? "none" : `2px solid ${accent}`,
          borderRadius: 8,
          padding: "6px 18px",
          minWidth: 150,
          textAlign: "center",
        }}
      >
        {label}
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div
          style={{
            fontFamily: FONT,
            fontWeight: emphasis ? 800 : 600,
            fontSize: emphasis ? 76 : 50,
            color: "#ffffff",
            lineHeight: 1.05,
          }}
        >
          {item.title}
        </div>
        {item.time ? (
          <div
            style={{
              fontFamily: FONT,
              fontSize: 30,
              color: accent,
              fontWeight: 700,
              marginTop: 6,
            }}
          >
            {item.time}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export const NowNextLater: React.FC<NowNextLaterProps> = ({
  channelName,
  accent,
  now,
  next,
  later,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const outro = interpolate(
    frame,
    [durationInFrames - 20, durationInFrames - 1],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const kicker = spring({ frame, fps, config: { damping: 200 } });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0b0f" }}>
      {/* accent side bar */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 14,
          background: accent,
        }}
      />
      {/* subtle corner glow */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(120% 120% at 0% 0%, rgba(255,255,255,0.06), transparent 60%)",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: outro,
          padding: "120px 120px 120px 160px",
          justifyContent: "center",
        }}
      >
        <div style={{ opacity: kicker, marginBottom: 56 }}>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 32,
              letterSpacing: 8,
              fontWeight: 700,
              color: accent,
            }}
          >
            COMING UP
          </span>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 32,
              letterSpacing: 8,
              fontWeight: 700,
              color: "#6b7080",
              marginLeft: 16,
            }}
          >
            ON {channelName.toUpperCase()}
          </span>
        </div>
        <Slot label="NOW" item={now} accent={accent} delay={6} emphasis />
        <Slot label="NEXT" item={next} accent={accent} delay={18} />
        <Slot label="LATER" item={later} accent={accent} delay={30} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
