# station-curator

A curator and programmer for the ErsatzTV channels. It keeps each channel's
collection **fresh** (rotating out what's gone stale), **on-charter** (filling
with what actually fits), and **rerun-safe** (no finales, no part 2 of a
two-parter), and it shortlists what's worth acquiring next.

It is deliberately small: one container, one SQLite state file, and **zero
dependencies** — stdlib Python throughout, including the LLM call.

---

## How it fits the stack

The curator's only database write is `CollectionItem` — collection membership.
The one other thing it does to ErsatzTV is repair dead air, through ErsatzTV's
own HTTP API (see *Safety notes*).

```
charters.toml ──► curator ──► ErsatzTV Collections ──► Blocks / Schedules ──► Channels
                     ▲                                  (yours, untouched)
                     ├── ErsatzTV DB   (library, genres, tags, ratings, playout)
                     ├── Jellyfin      (what you actually watched)
                     ├── Radarr/Sonarr (what you already own)
                     └── TMDB          (what exists that you don't)
```

That single integration point is the whole design. ErsatzTV etags collections,
so it notices membership changes and rebuilds the affected playout itself —
no restart, no forced rebuild, no schedule surgery. Your blocks, templates,
filler presets, watermarks and pre-roll config keep working exactly as
configured, and simply consume collections that are now kept fresh.

## What a cycle does

1. **Snapshot airings.** ErsatzTV prunes `PlayoutItem` rows as the playout
   window rolls forward, so airplay history is copied into the curator's own
   ledger before it disappears. `FillerKind = 0` only — your junction and
   feature cards are not airings of anything.
2. **Expire cooldowns.**
3. **Read the library**, classify shows as serialised or episodic, and judge
   every episode for rerun safety.
4. **Per channel: retire, then fill.**
5. **Repair dead air** — rebuild any channel still booked onto deleted files.
6. **Shortlist acquisitions**, write `proposals.md`, push a summary.

## Staleness

A title leaves rotation when any of these is true:

| signal | source | default |
|---|---|---|
| aired too often | curator ledger | 4 airings in 30 days |
| in rotation too long | curator ledger | 60 days |
| watched on demand recently | Jellyfin | within 21 days |

Retired titles go to cooldown (90 days) and can't come straight back. Two
guards stop a channel from lurching: `min_size` (never shrink past it) and
`max_churn` (a cap on adds + removes per run).

## Rerun safety

The point of the existing hand-built "superhero shows without finales"
collection, automated. Three deterministic signals, no LLM:

- **multi-part** — `"The Cat and the Claw (1)"`, `"Lost in Parking Space, Part
  One"`, `"...Mummies Part II"`
- **finale** — last episode of a season, or `"Finale"` in the title
- **premiere** — episode 1 (off by default)

Matching the substring `Part` would be wrong: this library contains *Drunken
Office Party*, *Runaway Float / Partners*, *Third Party Insourcing* and *The
City Part of Town*. Every pattern is word-anchored and requires a real part
number, and all four are correctly ignored. On the current library, of 1932
episodes: **1686 rerun-safe** on the deterministic signals alone (92 multi-part,
149 finales held back). With serialisation classification enabled, a further
549 episodes of story-arc shows are excluded from channels that ask for it,
leaving 1209.

> **One honest caveat.** A "series finale" is really *the last episode you
> own*. For a show whose later seasons aren't in the library, that episode is
> held back even though it isn't a finale. This errs toward caution — the cost
> is one benched episode per show. Set `skip_finales = false` per channel if
> you'd rather not.

Whether a show is *serialised* at all can't be read from genres — *Arcane* and
*American Dad!* are both Animation. That judgement is the one place a
show-level LLM call is used, cached forever per show. It only bites on channels
with `reruns.skip_serialized = true`; Toonami and the superhero block set it
false, because there a finale is the real hazard, not continuity.

## Collection shapes

`kind` in `charters.toml`, matching how the collections are already used:

| kind | contents | example |
|---|---|---|
| `movie` | films | `black films greatest hits` |
| `show` | whole series, expanded by ErsatzTV | `anime`, `kids shows` |
| `episode` | individual rerun-safe episodes | `superhero shows without finales` |
| `auto` | inferred from current contents | `linear action shows` |

For `episode`, the charter ranks **shows**, then rerun-safe episodes of the
shows that qualify are interleaved round-robin so one series can't dominate,
and spread within each show so it isn't always episode 1.

## Programming order

Membership is *what* plays; this is *what order*.

**ErsatzTV already does most of this natively** — every schedule item has a
`PlaybackOrder` (Chronological, Random, Shuffle, ShuffleInOrder,
MultiEpisodeShuffle, SeasonEpisode, RandomRotation, Marathon) and Marathon
takes a batch size for "N episodes of one show, then the next show". Set those
in the UI. The curator does not duplicate them; your channels currently use
Random / Shuffle / ShuffleInOrder and it leaves those alone.

The curated orders exist only for what ErsatzTV **can't** express, because they
need airplay history (which it doesn't keep) or rerun safety:

| `order` | what it does |
|---|---|
| `none` *(default)* | leaves ErsatzTV's PlaybackOrder alone |
| `rested` | least-aired first — lead with what you haven't seen lately |
| `heavy` | most-aired first — the hits, on purpose |
| `doubles` | `batch` in-order episodes per show, then the next show |
| `sequential` | in-order within each show |
| `shuffle` | deterministic — stable across runs |
| `ersatztv` | hand a previously-curated collection back |

`doubles` is Marathon's idea with the curator's advantage: the pool is only
rerun-safe episodes, so a pair never straddles a finale or splits a
two-parter. Toonami with `batch = 2` produces:

```
  0  The Darwin Incident              S1E3
  1  The Darwin Incident              S1E4
  2  PLUTO                            S1E1
  3  PLUTO                            S1E2
  4  Inuyashiki                       S1E1
  5  Inuyashiki                       S1E2
```

Order is written as `CollectionItem.CustomIndex` with the collection's
`UseCustomPlaybackOrder` flag. That lane is unused on this server (zero rows
carry a `CustomIndex`), so adopting it disturbs nothing — and `order =
"ersatztv"` clears the flag and the indexes to give control back.

`shuffle` is deliberately *not* `random.shuffle`: the same membership must
produce the same order every run, or each cycle would reshuffle a channel
ErsatzTV has already built a playout from.

## Where the LLM is used — and where it isn't

Deterministic: charter matching, staleness, finales, multi-parters, dedupe,
rating gates, programming order. **Never calls out.**

Two providers, chosen by whichever key is set — **Gemini by default**, and it
needs no package at all (plain REST over stdlib `urllib`, structured output via
`responseSchema`). Claude is available via `CURATOR_LLM_PROVIDER=anthropic`,
which needs its SDK: rebuild with `--build-arg WITH_ANTHROPIC=1`. The cache is
provider-agnostic, so switching doesn't re-ask what's already been answered.

### Switching to Claude

Two settings and one rebuild flag. The Anthropic SDK is not in the image by
default -- that is what keeps the container dependency-free -- so the switch
needs `--build-arg WITH_ANTHROPIC=1`:

```bash
# 1. in the server .env
ANTHROPIC_API_KEY=sk-ant-...
CURATOR_LLM_PROVIDER=anthropic

# 2. rebuild with the SDK, then restart
docker compose -f docker-compose.complete-homeserver.yml \
  build --build-arg WITH_ANTHROPIC=1 station-curator
docker compose -f docker-compose.complete-homeserver.yml up -d station-curator
```

Forget the build arg and the curator logs an explicit error naming that
command, then runs deterministically rather than pretending it has no
opinions. Switching back to Gemini is just the two env vars again; the
rebuilt image keeps working either way.

**The cache is provider-agnostic**, so switching does not re-ask anything
already answered -- the 87 show classifications and every taste verdict
carry over. Only genuinely new questions go to the new provider.

Defaults to `claude-opus-5`; override with `CURATOR_LLM_MODEL`. Requests use
`effort: "low"` (these are simple classifications) and structured outputs, and
deliberately send no `temperature` or `budget_tokens` -- both are rejected on
Opus 5.

**Model choice, learned the hard way.** `gemini-2.5-flash` returns 404 for new
keys ("no longer available to new users"), and `gemini-flash-latest` served
repeated 503s under the real 87-show batch. The default is `gemini-3.6-flash`,
which Google's own deprecation message points at and which completed the batch
reliably. Transient 429/5xx are retried twice with backoff; a model that is
eventually retired returns a 404 naming its successor, and until you change
`CURATOR_LLM_MODEL` the curator simply runs deterministically.

Measured on this library: 87 shows classified in **3 calls, ~80 seconds**, then
never again — the answers are cached per title. It agreed with a hand-checked
sample 9/10, calling Arcane, Invincible, Chainsaw Man and PLUTO serialised and
American Dad!, Bob's Burgers and Batman: TAS episodic. (It read Black Mirror
correctly as an anthology, and hedged Ghost in the Shell: SAC to serialised,
which is fair — it mixes standalone and arc episodes.)

The LLM is used for exactly two things, both cached in the ledger forever:

1. **Is this show serialised?** Once per show, for the life of the server.
   87 shows ≈ 3 batched calls, ever.
2. **Does this actually belong?** Only for channels with `taste_gate = true`,
   and only for candidates that already passed scoring.

The second exists because some charters simply aren't expressible in metadata.
"Black American cinema" is subject matter, not a genre — scoring alone offers
*Fast Times at Ridgemont High* for the BET channel. Scoring proposes; Claude
strikes the ones that only look right on paper.

**Without `ANTHROPIC_API_KEY` everything still runs**: shows are treated as
episodic and taste gates are skipped. A failed call is logged and ignored —
it narrows, it never gatekeeps.

Where metadata *can* do the job, it does. Content ratings are ~98% populated
here, so `ratings = ["TV-Y", "TV-Y7", ...]` keeps the kids channel free of
*Aqua Teen Hunger Force* far more reliably — and for free — than taste ever
would.

## Acquisition: proposals only

Nothing is downloaded. Ever. Candidates come from TMDB Discover using the
charter's own filters, are deduped three ways (Radarr/Sonarr by TMDB id, the
library by normalised title + year, the ledger by what's already been
proposed), and land in `/state/proposals.md` with TMDB links, plus an optional
ntfy push. You decide what to actually grab.

Requires a free `TMDB_API_KEY`; without one this step is skipped.

## Configuration

`charters.toml` is the only file you normally edit — TOML so it takes comments,
parsed with stdlib `tomllib`. Unknown keys are warned about rather than
silently ignored, because a typo in a hand-edited charter is a bad failure
mode.

Environment (all optional except the DB path):

| variable | default | purpose |
|---|---|---|
| `ERSATZTV_DB` | `/ersatztv/ersatztv.sqlite3` | ErsatzTV database (read-write) |
| `CURATOR_STATE_DB` | `/state/curator.sqlite3` | ledger |
| `CURATOR_PROPOSALS` | `/state/proposals.md` | shortlist |
| `CURATOR_INTERVAL` | `21600` | seconds between cycles (6h) |
| `CURATOR_DRY_RUN` | `false` | log decisions, write nothing |
| `JELLYFIN_API_KEY` | — | watch data |
| `RADARR_API_KEY` / `SONARR_API_KEY` | — | acquisition dedupe |
| `TMDB_API_KEY` | — | discovery |
| `GEMINI_API_KEY` | — | the two cached judgements above (default provider) |
| `ANTHROPIC_API_KEY` | — | same, if using Claude instead |
| `CURATOR_LLM_PROVIDER` | whichever key is set | `gemini` \| `anthropic` |
| `CURATOR_LLM_MODEL` | provider default | `gemini-3.6-flash` / `claude-opus-5` |
| `CURATOR_NTFY_URL` | — | push on new proposals |

## Try it before trusting it

`CURATOR_DRY_RUN=1` logs every add and retire and writes nothing:

```bash
docker compose -f docker-compose.complete-homeserver.yml \
  run --rm -e CURATOR_DRY_RUN=1 station-curator
```

Read the log, tune `charters.toml`, then run it for real. Because state lives
in one file, `rm /state/curator.sqlite3` resets the curator's memory without
touching ErsatzTV.

## Safety notes

- **Unplayable content is swept, not curated.** Deleting a file doesn't remove
  it from a collection — ErsatzTV marks the item `FileNotFound` and leaves the
  membership alone, so it keeps a slot and can still be scheduled, which is
  dead air. The same goes for a series the library lists but holds no episodes
  of (an empty season folder Jellyfin matched anyway, like *Living Single*):
  the Show itself looks healthy, but it expands to nothing at playout. Both are
  removed from any managed collection, ignored as candidates, and — since they
  aren't really owned — proposable again. Empty series are listed in the report
  so you can fetch the episodes or delete the folder.
- **Deleted episodes still booked on a channel are rebuilt away.** Sweeping
  can't reach these: a `show` collection keeps the series when only some of
  its episodes are deleted, so no etag changes and ErsatzTV never rebuilds —
  the already-built ~48h window keeps airing the missing files as dead air. And
  a rebuild alone doesn't help, because ErsatzTV schedules `FileNotFound`
  episodes like any other until they leave its trash. So each cycle, any
  channel with upcoming slots on unplayable media gets ErsatzTV's trash
  emptied (`POST /api/maintenance/empty_trash`), then its playout reset
  (`POST /api/channels/{n}/playout/reset`), then Jellyfin's guide refreshed.
  A reset reshuffles that channel's lineup from now on. If more than 25% of the
  library is unplayable at once, that's a missing drive, not deletions — the
  curator logs an error and leaves the trash alone.
- **Database writes are confined to `CollectionItem`.** Collections are a two-column
  join table; scheduling tables are stateful and version-specific, so they're
  left alone.
- ErsatzTV's database is WAL-mode, so short writes coexist with the running
  container; every connection sets a 30s `busy_timeout`.
- The library contains genuine duplicates (*The Prince of Egypt* appears three
  times under three media ids). Identity for scheduling is title + year, not
  media id, so a channel can't play the same film twice — worth cleaning up in
  Jellyfin regardless.
