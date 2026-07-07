import React from "react";
import { Composition } from "remotion";
import { NowNextLater } from "./NowNextLater";

// 8 seconds at 30fps. Change here (and TARGET_FPS stays 30 to match the
// station-processor output) if you want longer/shorter junction cards.
const FPS = 30;
const DURATION_IN_FRAMES = 8 * FPS;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="NowNextLater"
      component={NowNextLater}
      durationInFrames={DURATION_IN_FRAMES}
      fps={FPS}
      width={1920}
      height={1080}
      // Shown when you preview in `npx remotion studio`; overridden at render
      // time by the live schedule data from generate.mjs.
      defaultProps={{
        channelName: "My Channel",
        accent: "#e50914",
        now: { title: "The Program Now Playing", time: "8:00 PM" },
        next: { title: "Whatever Comes Next", time: "8:30 PM" },
        later: { title: "And Later Tonight", time: "9:00 PM" },
      }}
    />
  );
};
