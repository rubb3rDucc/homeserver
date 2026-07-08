# public/ — Remotion static assets

Drop asset files here and reference them in a composition with
`staticFile("name.ext")`. Remotion serves this folder at the site root, so it
works in both `remotion studio` and headless renders.

Suggested files for the NowNextLater junction card:

| File            | Used for                        | Prop / env         |
| --------------- | ------------------------------- | ------------------ |
| `broll.mp4`     | moving background behind text   | `backgroundSrc` / `BROLL_SRC` |
| `ambient.mp3`   | ambient music bed               | `musicSrc` / `MUSIC_SRC`      |
| `announcer.mp3` | announcer voiceover             | `voiceoverSrc` / `VOICEOVER_SRC` |

To preview a background in Studio, set the default in `src/Root.tsx`:

```ts
import { staticFile } from "remotion";
// ...
backgroundSrc: staticFile("broll.mp4"),
```

In production the render loop passes `BROLL_SRC` (a container path or URL)
instead — see the `render-lane` service env in the compose file.
