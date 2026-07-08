import React from "react";
import { AbsoluteFill, OffthreadVideo } from "remotion";

// Shared CN City design language for every card in the lane. NowNextLater still
// inlines its own copy of these (it's the reference); Ident + Feature build on
// this module so the look stays consistent.

// Liberation/DejaVu are installed in the container (headless Chromium has no
// fonts otherwise, and text would render as empty boxes).
export const FONT = "Liberation Sans, DejaVu Sans, Arial, Helvetica, sans-serif";

// CN City was pillarboxed 4:3 inside the 16:9 frame.
export const AR_W = 4;
export const AR_H = 3;

// White glow used on the era's illuminated type. `amount` is 0..1.
export const glowText = (amount: number) =>
  `0 0 ${amount * 26}px rgba(255,255,255,${amount})`;

export const initialsOf = (s: string) =>
  s
    .replace(/[^A-Za-z0-9 ]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("") || "•";

// Pillarboxed 4:3 stage with the CN City background: a muted/desaturated B-roll
// clip (or a city-haze gradient fallback), the era's daylight haze, and an
// optional legibility scrim. Content goes in `children` and sits over it all.
export const Stage: React.FC<{
  width: number;
  height: number;
  backgroundSrc?: string;
  scrim?: "right" | "center" | "none";
  children?: React.ReactNode;
}> = ({ width, height, backgroundSrc, scrim = "center", children }) => {
  const boxW = (height * AR_W) / AR_H;
  const boxLeft = (width - boxW) / 2;
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <div
        style={{
          position: "absolute",
          left: boxLeft,
          top: 0,
          width: boxW,
          height,
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
        {scrim === "right" ? (
          <AbsoluteFill
            style={{
              background:
                "linear-gradient(90deg, rgba(15,20,34,0) 20%, rgba(15,20,34,0.42) 52%, rgba(15,20,34,0.72) 100%)",
            }}
          />
        ) : scrim === "center" ? (
          <AbsoluteFill
            style={{
              background:
                "radial-gradient(65% 65% at 50% 52%, rgba(15,20,34,0.6), rgba(15,20,34,0.24) 58%, transparent 82%)",
            }}
          />
        ) : null}
        {children}
      </div>
    </AbsoluteFill>
  );
};

// The cream character/logo disc with an independent white glow ring, exactly as
// used in NowNextLater. `glow` (0..1) drives the halo; `token` is an image URL
// (Jellyfin art), else `initials` are shown.
export const Medallion: React.FC<{
  size: number;
  glow?: number;
  token?: string;
  initials?: string;
}> = ({ size, glow = 0, token, initials }) => (
  <div style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
    <div
      style={{
        position: "absolute",
        inset: -Math.round(size * 0.045),
        borderRadius: "50%",
        border: `3px solid rgba(255,255,255,${glow * 0.9})`,
        boxShadow: `0 0 ${glow * (size * 0.22)}px ${glow * (size * 0.07)}px rgba(255,255,255,0.85)`,
        opacity: glow,
        pointerEvents: "none",
      }}
    />
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
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: FONT,
        fontWeight: 800,
        fontSize: size * 0.34,
        color: "#3a3a44",
        letterSpacing: 1,
      }}
    >
      {token ? (
        <img
          src={token}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            padding: size * 0.11,
            boxSizing: "border-box",
          }}
        />
      ) : (
        initials
      )}
    </div>
  </div>
);
