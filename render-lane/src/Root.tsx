import React from "react";
import { Composition } from "remotion";
import { NowNextLater } from "./NowNextLater";
import { Ident } from "./Ident";
import { FeaturePresentation } from "./FeaturePresentation";

const FPS = 30;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="NowNextLater"
        component={NowNextLater}
        durationInFrames={8 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          now: { title: "The Program Now Playing", time: "8:00 PM" },
          next: { title: "Whatever Comes Next", time: "8:30 PM" },
          later: { title: "And Later Tonight", time: "9:00 PM" },
        }}
      />
      <Composition
        id="Ident"
        component={Ident}
        durationInFrames={4 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          tagline: "Always On",
        }}
      />
      <Composition
        id="FeaturePresentation"
        component={FeaturePresentation}
        durationInFrames={6 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          title: "Blade Runner",
          year: "1982",
          rating: "R",
          runtime: "1h 57m",
        }}
      />
    </>
  );
};
