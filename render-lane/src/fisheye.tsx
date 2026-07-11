import React from "react";
import { delayRender, continueRender } from "remotion";

// A transparent title logo, gently bulged by a fisheye and given a white keyline
// outline so it reads on a dark disc. SVG feImage/feDisplacementMap does not
// deliver a map in Remotion's headless Chromium, so we remap on a <canvas> with
// the canonical equidistant fisheye projection (Geeks3D shader) and show the
// result as a normal <img>. Computed once per src (cached). Cross-origin sources
// taint the canvas -> we fall back to the plain (contain) logo.

const S = 256; // working resolution
// Full-barrel aperture keeps the whole logo (edge maps to edge); STRENGTH then
// blends that fisheye back toward identity for a *slight* dome (0 = none, 1 =
// full). 0.28 is a gentle bulge that never crops the logo.
const APERTURE = 178;
const STRENGTH = 0.28;

const cache = new Map<string, string>();

function distortLogo(img: HTMLImageElement, padding: number): string {
  const src = document.createElement("canvas");
  src.width = src.height = S;
  const sctx = src.getContext("2d")!;
  const iw = img.naturalWidth || S;
  const ih = img.naturalHeight || S;
  const avail = S - 2 * padding;
  const scl = Math.min(avail / iw, avail / ih); // contain-fit with padding
  const dw = iw * scl;
  const dh = ih * scl;
  sctx.drawImage(img, (S - dw) / 2, (S - dh) / 2, dw, dh);
  const sd = sctx.getImageData(0, 0, S, S).data;

  const out = document.createElement("canvas");
  out.width = out.height = S;
  const octx = out.getContext("2d")!;
  const dst = octx.createImageData(S, S);
  const maxFactor = Math.sin((0.5 * APERTURE * Math.PI) / 180);
  for (let j = 0; j < S; j++) {
    for (let i = 0; i < S; i++) {
      const u = (i + 0.5) / S;
      const v = (j + 0.5) / S;
      const xyx = 2 * u - 1;
      const xyy = 2 * v - 1;
      const dorig = Math.hypot(xyx, xyy);
      let su = u;
      let sv = v;
      if (dorig < 2 - maxFactor) {
        const dd = dorig * maxFactor;
        const z = Math.sqrt(Math.max(0, 1 - dd * dd));
        const r = Math.atan2(dd, z) / Math.PI;
        const phi = Math.atan2(xyy, xyx);
        // blend the full fisheye back toward identity for a slight dome
        su = u + STRENGTH * (r * Math.cos(phi) + 0.5 - u);
        sv = v + STRENGTH * (r * Math.sin(phi) + 0.5 - v);
      }
      const si = Math.min(S - 1, Math.max(0, (su * S) | 0));
      const sj = Math.min(S - 1, Math.max(0, (sv * S) | 0));
      const so = (sj * S + si) * 4;
      const oo = (j * S + i) * 4;
      dst.data[oo] = sd[so];
      dst.data[oo + 1] = sd[so + 1];
      dst.data[oo + 2] = sd[so + 2];
      dst.data[oo + 3] = sd[so + 3];
    }
  }
  octx.putImageData(dst, 0, 0);
  return out.toDataURL();
}

export const FisheyeLogo: React.FC<{ src: string; padding?: number }> = ({
  src,
  padding = 8,
}) => {
  const [out, setOut] = React.useState<string | undefined>(() => cache.get(src));
  React.useEffect(() => {
    if (cache.has(src)) {
      setOut(cache.get(src));
      return;
    }
    const handle = delayRender(`fisheye-logo ${src.slice(0, 40)}`);
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      let r = "";
      try {
        r = distortLogo(img, padding);
      } catch {
        r = ""; // tainted canvas -> plain fallback
      }
      cache.set(src, r);
      setOut(r);
      continueRender(handle);
    };
    img.onerror = () => {
      cache.set(src, "");
      setOut("");
      continueRender(handle);
    };
    img.src = src;
  }, [src, padding]);

  const distorted = !!out && out.startsWith("data:");
  return (
    <img
      src={distorted ? out! : src}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "contain",
        padding: distorted ? 0 : padding, // padding is baked in when distorted
        boxSizing: "border-box",
      }}
    />
  );
};
