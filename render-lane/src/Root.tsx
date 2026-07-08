import React from "react";
import { Composition, staticFile } from "remotion";
import { NowNextLater } from "./NowNextLater";
import { FeaturePresentation } from "./FeaturePresentation";

const FPS = 30;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="NowNextLater"
        component={NowNextLater}
        durationInFrames={15 * FPS}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          now: { title: "The Program Now Playing", time: "8:00 PM" },
          next: { title: "Whatever Comes Next", time: "8:30 PM" },
          later: { title: "And Later Tonight", time: "9:00 PM" },
          // Studio preview: drop a clip at render-lane/public/broll.mp4 and it
          // shows here. Set back to "" to use the CSS gradient fallback instead.
          backgroundSrc: staticFile("broll.mp4"),
          musicSrc: "",
          voiceoverSrc: "",
        }}
      />
      <Composition
        id="FeaturePresentation"
        component={FeaturePresentation}
        durationInFrames={12 * FPS}
        fps={FPS}
        width={1280}
        height={720}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          title: "Blade Runner",
          year: "1982",
          rating: "R",
          runtime: "1h 57m",
          specs: [],
          token: "",
          backgroundSrc: staticFile("broll.mp4"),
          musicSrc: "",
        }}
      />
    </>
  );
};
