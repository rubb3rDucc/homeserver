"""
The small amount of LLM this system uses.

Two jobs only, both of which metadata genuinely cannot answer:

  serialised?   Is this show a story-arc serial (where episode order matters)
                or episodic (rerunnable in any order)? Genres don't encode it
                -- "Arcane" and "American Dad!" are both Animation.
  worth it?     Given a channel's prose charter, is this candidate actually
                the kind of thing that belongs there?

Everything else -- charter matching, staleness, finale and multi-part
detection, programming order -- is deterministic and never calls out.

Every answer is cached in the ledger forever, keyed by the thing being judged.
A show is classified once in the life of the server; a candidate is judged
once, ever. After the first run this module makes roughly zero calls, which is
what "minimal" has to mean to be worth anything.

Two providers, chosen by whichever key is set:

  gemini      (default) plain REST over stdlib urllib -- no dependency at all
  anthropic   uses the official SDK, if it happens to be installed

Set no key at all and the curator runs fully deterministically: shows are
treated as episodic and taste gates are skipped.
"""

import json
import logging

from .net import HttpError, request

log = logging.getLogger("curator.llm")

BATCH = 40  # shows per request; 87 shows is 3 calls, once

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Schemas are written in the OpenAPI subset Gemini accepts (no
# additionalProperties). _strict() adds what Anthropic wants on top.
_SERIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "shows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "serialized": {"type": "boolean"},
                },
                "required": ["id", "serialized"],
            },
        }
    },
    "required": ["shows"],
}

_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "year": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["title", "year", "why"],
            },
        }
    },
    "required": ["suggestions"],
}

_VIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "fits": {"type": "boolean"},
                },
                "required": ["id", "fits"],
            },
        }
    },
    "required": ["verdicts"],
}


def _strict(node):
    """Anthropic's strict JSON schema wants additionalProperties: false."""
    if isinstance(node, dict):
        out = {k: _strict(v) for k, v in node.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_strict(v) for v in node]
    return node


class LLM:
    """Thin, cached, provider-agnostic. A failure degrades to no opinion."""

    def __init__(self, ledger, model: str, api_key: str, provider: str = ""):
        self.ledger = ledger
        self.api_key = api_key
        self.calls = 0
        self.client = None
        self.provider = (provider or "gemini").lower()
        self.model = model or self._default_model()

        if not api_key:
            self.provider = "none"
            return

        if self.provider == "anthropic":
            try:
                import anthropic
            except ImportError:
                log.warning(
                    "anthropic SDK missing -- running deterministically"
                )
                self.provider = "none"
                return
            self.client = anthropic.Anthropic(api_key=api_key)

    def _default_model(self) -> str:
        # gemini-3.6-flash: what Google's own deprecation message points at
        # (gemini-2.5-flash now 404s for new keys), and the one that actually
        # completed the real 87-show batch here -- gemini-flash-latest served
        # repeated 503s under the same load. If this is ever retired the 404
        # body names its successor; override with CURATOR_LLM_MODEL.
        return ("claude-opus-5" if self.provider == "anthropic"
                else "gemini-3.6-flash")

    @property
    def enabled(self) -> bool:
        return self.provider in ("gemini", "anthropic") and bool(self.api_key)

    # ---- transport -------------------------------------------------------- #
    def _ask(self, prompt: str, schema: dict) -> dict | None:
        if not self.enabled:
            return None
        try:
            if self.provider == "gemini":
                text = self._ask_gemini(prompt, schema)
            else:
                text = self._ask_anthropic(prompt, schema)
        except HttpError as exc:
            log.warning("%s call failed (%s) -- continuing without it",
                        self.provider, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - never break a curation run
            log.warning("%s call failed (%s) -- continuing without it",
                        self.provider, exc)
            return None

        if text is None:
            return None
        self.calls += 1
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("unreadable %s response: %s", self.provider, exc)
            return None

    def _ask_gemini(self, prompt: str, schema: dict) -> str | None:
        """
        Gemini REST. Structured output via responseMimeType + responseSchema,
        so the reply is guaranteed-parseable JSON rather than fenced prose.
        """
        payload = request(
            f"{GEMINI_URL}/{self.model}:generateContent",
            method="POST",
            headers={"x-goog-api-key": self.api_key},
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                    "temperature": 0,
                },
            },
            timeout=120,
            retries=2,   # model endpoints return a real 503 under load
        )
        candidates = (payload or {}).get("candidates") or []
        if not candidates:
            # A safety block returns 200 with no candidate; not an error.
            log.warning("gemini returned no candidate -- continuing without it")
            return None
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts) or None

    def _ask_anthropic(self, prompt: str, schema: dict) -> str | None:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
            # Simple classification: low effort keeps these calls cheap.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _strict(schema)},
            },
        )
        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("claude declined the request -- continuing without it")
            return None
        return next(b.text for b in response.content if b.type == "text")

    # ---- serialisation ---------------------------------------------------- #
    def serialized_shows(self, shows) -> set[int]:
        """
        Classify shows as story-arc serials. Returns the set of serial ids.

        Cached per title+year, so re-runs and library rescans cost nothing.
        The cache is provider-agnostic on purpose -- switching from Gemini to
        Claude doesn't re-ask what's already been answered.
        """
        verdict: dict[int, bool] = {}
        pending = []
        for show in shows:
            key = f"serial:{show.title.lower()}:{show.year or 0}"
            hit = self.ledger.cache_get(key)
            if hit is not None:
                verdict[show.media_id] = hit == "1"
            else:
                pending.append(show)

        if pending and not self.enabled:
            log.info(
                "%d show(s) unclassified and no LLM key -- treating as episodic",
                len(pending),
            )

        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            listing = "\n".join(
                f"{s.media_id}. {s.label()}"
                f"{' [' + ', '.join(sorted(s.genres)) + ']' if s.genres else ''}"
                for s in chunk
            )
            prompt = (
                "For each TV show below, decide whether it is SERIALIZED -- a "
                "continuing story arc where episodes must be watched in order "
                "and a random episode would confuse a viewer -- or EPISODIC, "
                "where most episodes stand alone and can air in any order.\n\n"
                "Judge the show as a whole. Shows with light continuity but "
                "self-contained episodes (most sitcoms, most procedurals, most "
                "monster-of-the-week cartoons) are EPISODIC. Serialized means "
                "the plot genuinely carries over, as in a prestige drama or a "
                "story-arc anime.\n\n"
                "Return one entry per show, using the numeric id given.\n\n"
                f"{listing}"
            )
            data = self._ask(prompt, _SERIAL_SCHEMA)
            if not data:
                continue
            by_id = {s.media_id: s for s in chunk}
            for entry in data.get("shows", []):
                show = by_id.get(entry.get("id"))
                if show is None:
                    continue
                flag = bool(entry.get("serialized"))
                verdict[show.media_id] = flag
                self.ledger.cache_set(
                    f"serial:{show.title.lower()}:{show.year or 0}",
                    "1" if flag else "0",
                )

        serials = {mid for mid, flag in verdict.items() if flag}
        if verdict:
            log.info(
                "serialisation: %d serial / %d episodic (%d call(s) this run)",
                len(serials), len(verdict) - len(serials), self.calls,
            )
        return serials

    # ---- taste ------------------------------------------------------------ #
    def vibe_filter(self, collection, brief, candidates,
                    namespace="tmdb") -> dict[str, bool]:
        """
        Judge candidates against a channel's prose charter.

        `candidates` are dicts with id/title/year/overview. Returns id -> fits.
        Anything not judged (no key, call failed) is absent from the result and
        must be treated as "keep" by the caller -- this narrows, it never
        gatekeeps on failure.

        `namespace` separates cache entries for TMDB candidates from those for
        titles already in the library, since their ids are different spaces.
        """
        out: dict[str, bool] = {}
        pending = []
        for cand in candidates:
            key = f"vibe:{namespace}:{collection}:{cand['id']}"
            hit = self.ledger.cache_get(key)
            if hit is not None:
                out[str(cand["id"])] = hit == "1"
            else:
                pending.append(cand)

        if not pending or not self.enabled:
            return out

        listing = "\n".join(
            f"{c['id']}. {c['title']} ({c.get('year') or '?'}) -- "
            f"{(c.get('overview') or 'no synopsis')[:240]}"
            for c in pending
        )
        prompt = (
            f"A TV channel is described as: {brief!r}\n\n"
            "For each candidate title below, decide whether it genuinely "
            "belongs on that channel. Be selective: a title that merely shares "
            "a genre does not fit. Judge tone and subject matter against the "
            "description.\n\n"
            "Return one verdict per candidate, using the id given as a string."
            f"\n\n{listing}"
        )
        data = self._ask(prompt, _VIBE_SCHEMA)
        if not data:
            return out

        for entry in data.get("verdicts", []):
            ext_id = str(entry.get("id"))
            fits = bool(entry.get("fits"))
            out[ext_id] = fits
            self.ledger.cache_set(
                f"vibe:{namespace}:{collection}:{ext_id}", "1" if fits else "0"
            )
        return out

    # ---- discovery -------------------------------------------------------- #
    def suggest(self, channel, playing, limit=12):
        """
        Ask for titles that belong on this channel, given what it plays.

        This is a much better recommender than TMDB genre filters -- "films
        like Menace II Society" is exactly the query metadata can't express.
        The catch is hallucination: models invent plausible titles. So nothing
        here is trusted. Every suggestion is resolved against Radarr/Sonarr
        (which return no match for an invented film) before it can become a
        proposal -- see discover.resolve.

        Cached on the channel plus a fingerprint of what it's playing, so a
        channel is only re-asked once its line-up has actually changed.
        """
        if not self.enabled:
            return []

        fingerprint = str(abs(hash(tuple(sorted(playing)))) % 10**12)
        key = f"suggest:{channel.collection}:{fingerprint}"
        hit = self.ledger.cache_get(key)
        if hit is not None:
            return json.loads(hit)

        era = ""
        if channel.charter.years:
            low, high = channel.charter.years
            era = (f"\nStay within {low}-{high}; a title outside that window "
                   "is not useful here.")
        listing = "\n".join(f"- {t}" for t in sorted(playing)[:40])
        prompt = (
            f"A TV channel is described as: {channel.brief or channel.name!r}"
            f"{era}\n\nIt currently plays:\n{listing}\n\n"
            f"Suggest {limit} more {'films' if channel.kind == 'movie' else 'series'} "
            "that genuinely belong on this channel -- the same sensibility, "
            "not merely the same genre. Do not repeat anything listed above.\n\n"
            "Only real, released titles. Give the exact release year. If you "
            "are unsure a title exists, leave it out."
        )
        data = self._ask(prompt, _SUGGEST_SCHEMA)
        out = (data or {}).get("suggestions", [])
        self.ledger.cache_set(key, json.dumps(out))
        return out
