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
        width={1280}
        height={720}
        defaultProps={{
          channelName: "My Channel",
          accent: "#e50914",
          // Studio-only: real logos in render-lane/public/logos (gitignored) to
          // test the outline treatment. Ghost in the Shell = the white-on-white
          // case; Initial D = a very wide logo; Venture Bros = a dark one.
          // Production render overrides these via inputProps from generate.mjs.
          // Studio-only: real logos in render-lane/public/logos (gitignored).
          now: {
            title: "Ghost in the Shell",
            time: "8:00 PM",
            token: staticFile("logos/ghost-in-the-shell.png"),
            tokenType: "logo",
          },
          next: {
            title: "Initial D",
            time: "8:30 PM",
            token: staticFile("logos/initial-d.png"),
            tokenType: "logo",
          },
          later: {
            title: "The Venture Bros.",
            time: "9:00 PM",
            token: staticFile("logos/venture-bros.png"),
            tokenType: "logo",
          },
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
