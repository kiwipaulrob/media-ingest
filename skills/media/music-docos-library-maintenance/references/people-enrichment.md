# Batch people enrichment (bios + headshots)

Enriching cast/crew bios across many rows at once — provider pass first, Wikipedia second, portraits throughout.

## Phase 1 — verify the title match before touching people

Person ids from a wrongly-matched row attach the wrong humans. Gate every row on its TMDB record before writing anyone:

- Fetch `/movie/{id}` (or `/tv/{id}`); require ~60% coverage of the record's distinctive tokens by the row/folder name (stopword-filtered) AND year ±1.
- Route failures to a check list, never through: same-name wrong variants, TV/movie namespace collisions (one id, two works — the library resolves the wrong side), collection ids sitting on movie rows, and dead ids returning no record.
- Credits carry person ids directly (`/movie/{id}/credits`) — use those; no person-search is needed and same-name search risk never enters.
- **Use MusicBrainz as the identity bridge for obscure names the title gate cannot settle.** `GET https://musicbrainz.org/ws/2/artist/?query=artist:"<name>"&fmt=json` (free, keyless, ~1 req/s) returns MBID + country + a `disambiguation` line ("New Zealand guitarist/bassist/engineer") that resolves same-name collisions before any bio is written — run it on every gap name, not just the ambiguous ones, because the pass is cheap and the collision it prevents is a wrong-life write. It carries no prose and usually no links, so use it for identity only: confirm country/role match the credit, then take the bio from the normal prose chain; MBIDs also resolve onward to Wikidata entries.

## Phase 2 — write credits, pin ids onto Person rows

- Write `People` (name, provider `character` role, Director/Producer from crew) onto each item via the full-DTO round-trip; fall back to a `People`-only body on 400.
- Pin the Tmdb id onto each **Person row** (the library-global person item), not the embedded credit entry — ids set on embedded entries do not persist. Skip re-pinning when the row already carries the same id.
- Run the `Refresh People` scheduled task once at the end, not per title; recount bio/portrait coverage per credit afterwards.

## Phase 3 — Wikipedia for whatever the provider cannot fill

TMDB holds empty shells even for famous names, which a refresh can never fill — that is the Wikipedia queue. Rules:

- Exact-title match only (opensearch title equals the name; reject near-miss titles) — a near-miss attaches the wrong life. **The title gate cannot catch disambiguation pages: their title IS the exact name, so they pass by construction.** Reject them on content instead — any extract whose first ~400 chars read "<name> may refer to" / "can refer to" / a bare list of senses is a disambiguation page, not a biography. (Proven cost: 29 of 542 names 400'd on this, because a disambiguation page's intro extract is a bulleted sense-list with no prose to write.)
- **Exact-title rejects can be real aliases — queue them, never drop them.** The gate is name-shape-blind by design (a near-miss usually IS a different person), but stage names, birth names and married names defeat it: "Susie Bick"→"Susie Cave", "Jellybean Benítez"→"John Benítez" are the same humans, while "Mike Christie"→"Michael Christie" usually is not. Emit every reject to a human-confirm queue pairing the two names with the TMDB department and film count — confirm writes the page bio, reject retires the name. Never auto-write through the gate, and never discard a reject silently. **A name-shape approval is not enough: check the target's birth year against the credit.** The dangerous alias class is the plausible-looking truncation or nickname (`Francis Hanly` → `Frank Hanly`, `James Hale` → `James Tracy Hale`) — both approved on name similarity alone and both wrote a 19th-century politician onto a living crew member. Before approving a pair, confirm the target page states a birth year compatible with a modern career (or states none); a pre-1900 birth on a contemporary credit means the pair is wrong however close the names look.
- **Classify bio writes by length, not by a prose-sized threshold.** >= 80 chars = full bio (DONE); 15–79 = Wikidata one-liner (PARTIAL — written and locked by design, an obviously-short Overview a later pass may improve); < 15 = write failure. A single prose-sized bar mislabels successful one-liners as failures and feeds them to the retry queue forever.
- **Person-row POSTs can 400 on fields the person updater rejects** — the full item DTO carries fields the person endpoint refuses. On 400, retry with a minimal body (`Name` + `Overview` + `LockedFields` only); never abandon the write or substitute a guess.
- Never clobber: skip any row whose bio arrived since the worklist was built, and lock hand-written bios (`LockedFields: ["Name","Overview"]`). **Surgical-write protocol for every targeted edit (bio or otherwise):** GET the DTO fresh from the user-scoped route immediately before the write — never reuse a stale body and never use a search-result DTO as the write base (trimmed keys write back as nulls and wipe fields you never touched); change only the target fields; always include `Name` (an absent `Name` is stored as null); omit `Genres` (its shape 400s the whole update); on 400 retry minimal-body and layer fields one at a time; after every POST re-read the row and diff the fields you did NOT intend to change (`Name`, `ProviderIds`, people count, `ImageTags`) — a targeted edit that alters anything else is a failed write, roll it back from the re-read.
- Portraits: page image (never SVG, minimum ~15 KB) uploaded Base64, but only when the row has no image; verify researcher-grade risks by eye (a same-name sportsman is the classic false positive — check the file description before uploading). The upload body MUST be Base64-encoded bytes with `Content-Type: image/jpeg` — raw bytes 500 with `FormatException` in `FromBase64Transform` (the controller reads the body through a Base64 CryptoStream); proven pattern is `base64.b64encode(raw)` → POST → 204, then byte-verify the served image (`GET /Items/{id}/Images/Primary/0` length equals the uploaded length). TMDB `profile_path` is usually null for obscure names, so the Wikipedia thumbnail is the working portrait source in practice, not the fallback.
- **Verify locks with a direct item GET, never a search.** Search-endpoint DTOs omit `LockedFields` (reads back null/empty even when set) — confirm `Name`/`Overview` locks landed via `GET /Users/{uid}/Items/{personId}?Fields=LockedFields` before calling a bio locked.
- **Batch the API call, not the loop.** One MediaWiki `action=query&titles=A|B|C|…` carries up to 20 names and returns extracts + thumbnails + missing-page flags together; that is ~9× faster than one summary call per name and is what makes a 1,000-name queue finish in minutes instead of hours. Validate the exact title from the *response* (normalized compare against the sent name) — a separate opensearch lookup doubles the request rate and is what trips the throttle into minutes-per-name backoffs. **A batched result is a tuple, not a string** — it carries `(extract, thumbnail)`, so unpack both (`bio, thumb = …`) and write only the string half; assigning the tuple itself to Overview serialises a JSON array where the server expects a string and every such write 400s with `JsonStringConverter read too much or not enough`. That converter error on `$.Overview` is the tell that a non-string reached the field — read the 400 body before reshaping the payload again, it names the failing field.
- **Wikimedia rate limits are IP-wide, not per-token.** A heavy pass throttles the whole IP for the reset window (~30–60 min); no retry strategy clears it, and continuing to hammer it only extends the block. When the Action/REST APIs 429, stop and wait the window out rather than retrying — and sequence any further Wikimedia work after the quiet period.
- **Wikidata is a separate rate domain and runs in parallel with Wikipedia.** `wbsearchentities` (`https://www.wikidata.org/w/api.php?action=wbsearchentities&language=en&search=<name>`) is throttled independently, so it is usable *while* Wikipedia is down. It yields one-line occupation descriptions ("Dutch keyboardist, born 1945") rather than full prose — write them locked as a short Overview, leave them obviously short so a later pass can improve them, and do not expect portraits (Wikidata thumbnails are hit-or-miss). Hit rate on the long tail is ~12–15%, so run it as a cheap first filter over the whole queue before spending the slower Wikipedia pass on the survivors.
- Pace ~4 s between names; honour `Retry-After` with retries; run queues over ~50 names as tracked background jobs with per-name progress logs so a kill loses no record of what landed (writes land inline; the log is the audit trail). Log every outcome line (DONE and SKIP) at the branch that decides it — a log line only at loop end never fires for early-`continue` skips, so the count silently disagrees with the queue. Run the worker unbuffered (`python3 -u`) or a killed run's final summary is lost with the buffer. Resume by replaying the progress log: skip names already logged DONE, re-slice the remainder into bounded chunks, and merge per-chunk result files — a killed run must never re-process names it already wrote. Never trust a shell-chained completion marker (`worker.py; echo FINISHED`) — the marker fires even when the worker was killed, so a dead run reads as finished with no summary; completion is proven only by the worker's own final log line and summary output.

## Phase 3a — Last.fm as the primary prose source after TMDB

Last.fm's artist API (`http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist=<name>&api_key=<key>&format=json`) is free, generous with limits, and carries a `bio.summary` per artist — often the only prose that exists for obscure musicians where TMDB/Wikipedia return empty shells.

- Use it as the FIRST fallback when TMDB and Wikipedia both come back empty; the skill's existing example run (Ayuli case) used AllMusic as fallback, Last.fm slots in the same position one step earlier.
- Response carries `image` URLs too — but the largest entry is very often Last.fm's **default placeholder**, identical across unrelated artists (every lookup in a test run returned the same content hash). Compare the hash against other names in the same run: a shared hash means there is no portrait, so skip it rather than uploading a grey placeholder onto a person row. Only a distinct hash ≥ 15 KB is usable.
- **The key is present on this install** — `LASTFM_API_KEY` lives in `/root/.hermes/.env` (app registered as `Hermes`), so Phase 3a is ACTIVE, not an optional pass. Keyless calls answer 403; if the key is ever lost, re-register at `last.fm/api/account/create` rather than debugging the endpoint.
- **Last.fm is the highest-yield prose source for music-domain person rows.** A full sweep of hundreds of names returned 22 of 24 full bios from Last.fm against 2 from Wikipedia — run it first and treat the slower batched Wikipedia pass as the long tail (it mostly contributes stubs, disambiguation pages and alias rejects for musicians).
- **Band-page targets supply prose only — never a portrait.** When an approved alias resolves to a band page (`Anvil (band)`, `The Dakotas`, `Jan and Dean`), the page image is a group photo; writing it onto a person row is wrong-life imagery. Write the bio, skip the image, and log it distinctly (`portrait-skipped-band`) so the gap is visible rather than silent.
- Strip HTML tags from `bio.summary` (`re.sub(r'<[^>]+>', '', ...)`) — Last.fm returns `<a href=…>` links inline; truncate at the first `Read more on Last.fm` if present.
- If the key is ever missing, this phase degrades to a pass — fall through to Wikipedia rather than retrying the endpoint.
- **Reject multi-artist and sense-list blurbs as bios.** Last.fm writes placeholders for shared names: "There are at least three artists with this name…", numbered lists ("1. <name> is an American male vocalist…"), and "may refer to" pages. These pass a length check and read as prose at a glance, so gate on content — reject any bio whose opening matches those shapes, and keep the record's portrait (an image can be right when the prose is not).
- **The API key is user-assisted, not automatable.** Last.fm key registration sits behind account signup with a reCAPTCHA and a password-strength policy — have the user register at `last.fm/api/account/create` (the form needs only an app name + one-line description) and store the key as `LASTFM_API_KEY` in `/root/.hermes/.env`. A vault-saved password that fails the site's strength policy is not fixable from the agent side — hand the signup back rather than retrying the form.
- Biography prose source priority: **TMDB → Last.fm → Wikipedia → AllMusic → Wikidata (one-liner) → hand-written from research**. Portraits: **TMDB → Last.fm image → AllMusic → Wikimedia Commons → Discogs (identity only) → AllMusic page image**. Discogs never supplies prose.

## Phase 3b — Discogs for musicians the providers miss (portraits + identity)

Run at ingest for every music title and in sweeps for bio-less person rows. TMDB holds empty shells for obscure musicians (proven: Alex Ayuli, TMDB 5216278, no bio no portrait) and Wikipedia has no page — that is the Discogs queue.

- Discogs artist pages carry no biography prose — mine them for PORTRAIT + identity (aliases, groups, labels, years), never prose. Bio text still comes from TMDB/Wikipedia/AllMusic; Discogs answers "what does he look like" and "which band is he".
- Discogs bot-walls direct fetches (challenge page, no og tags) — extract via the web_extract service, and portraits via AllMusic artist pages (`fastly-s3.allmusic.com` image URLs) as fallback.
- Upload portrait Base64 to the Person row only when imageless; byte-verify served bytes. Hand-write the bio from the best prose source via full-DTO + lock (proven: Ayuli 306-char bio + portrait, both verified).
- Never invent personal details — a channel-level bio (sourced tagline + series description) beats a guessed name; report identity gaps rather than filling them from memory.

## Phase 3c — hand-research sources by gap class (after providers miss)

Provider misses fall into three classes — route each to its source, never all through one site:

- **NZ / Flying Nun circle → AudioCulture first.** `audioculture.co.nz/people/<slug>` carries commissioned, bylined, dated long-form profiles (tens of thousands of chars) with member lists and label links — biography-grade and safe to summarize into locked bios. Confirm identity first with the MusicBrainz bridge (country NZ + disambiguation line), because display spellings differ from true names (single-p vs double-p).
- **Sarah Records circle → sarahrecords.org.uk first.** The official archive carries first-person label history (gig flyers, flexidisc/fanzine lineage, band posts) — primary-source prose, summarize with attribution. Back it with twee.net band histories, elefant.com artist bios, and Wikipedia for the band overview.
- **Crew gaps (directors/producers/editors) → film sources, never music sources.** The top multi-film gaps are crew, not musicians: use Criterion author pages, IMDb name pages (credits + self-bio), and official/publisher bios. AllMusic is musician-only — it returns wrong same-name humans on crew names, so never run crew gaps through it.
- **NZ On Screen via collections, not person URLs.** Person-path URLs 404; the working shape is `/collection/<slug>/` (curated, with background essays) plus the site's profile index. Use it for the film/TV side of NZ gaps (founders, directors) and BBC-aired doco synopses alongside BBC programme pages.
- **Retired: l-t-m.eu.** The domain no longer resolves on either scheme — do not spend passes on it; the Sarah archive above replaces it.

## Provider chain — the current implementation (`media_lib.providers`)

One adapter per source, each exposing `search()` → candidates and `fetch()` → a record with **per-field provenance**. `chain.resolve(name, kind)` merges them and returns `fields`, `provenance`, `conflicts`, `rejected` and `identity`. Use it instead of a new script; run it from `media-ops` (`python3 -c "from media_lib.providers import chain; ..."`) or via the `meta_resolve` tool.

- **Prose priority:** TMDB → Last.fm → Genius → Wikipedia. **Portraits:** TMDB → Deezer → Last.fm → TheAudioDB → Wikipedia. Every merged field records which provider supplied it, so a bad value is traceable and revertible.
- **MusicBrainz is identity only** — MBID, country, type, disambiguation and documented **aliases**. It never carries prose; keep it out of the prose/portrait order so nothing is tempted to trust it.
- **TheAudioDB never supplies prose.** Its same-name collisions are undetectable: a single exact-name match can be a completely different act (an indie-pop band name resolves to a French power-metal band with a 2,000-char bio and no ambiguity signal). Use it for banners and thumbnails only. Its shared test key `123` works keyless; a private key is now Patreon-gated and not worth it. **Enforce that in CODE, not by ranking** — a priority list alone re-admits it through the merge's "providers not named in a priority order still count, ranked last" fallback, and two- and three-thousand-character TheAudioDB bios reached rows that way. Keep an explicit blocked-provider set that the biography merge skips even as a last resort, and pin it with a test.
- **Deezer is portraits only** — no prose field at all; its XL image is a real photo where Last.fm usually serves the shared placeholder.
- **Genius:** artist search is keyless; descriptions need a client token, so a missing `GENIUS_API_TOKEN` yields candidates and images but no prose (fall through, do not debug it).

**The identity gate is the load-bearing rule, not the ranking.** Provider search is fuzzy by design, so a returned record is a *suggestion*: gate every record on name identity before it can contribute a field.

- Compare normalised names (alphanumeric, lowercased) and **strip parenthetical disambiguators first** — `The Edge (Guitarist)` is the same person, while `Betty Palik` → `Betty White` and `Nick Banks` → `Nickelback` are not. Without the strip, legitimate disambiguated pages are falsely rejected.
- Accept a mismatch only when MusicBrainz lists the found name as a **documented alias** — that is how `William Hammond Talbot` (Billy Talbot) and `Nicholas David Banks` pass while lookalikes still fail.
- **Fetch only the best candidate per provider.** Fetching every fuzzy hit drags unrelated names (a band's near-namesakes, tribute acts, similarly-spelled artists) into the merge and floods the conflict list.
- Report rejections rather than hiding them: a `rejected` list naming provider + found name + reason is what makes the gate auditable.
- Restrict `conflicts` to **identity-bearing fields** (name, year, birth/formed year, provider ids, MBID). Per-provider metrics (listeners, album count) and per-provider URLs differ by nature and are noise in a conflict list. A name conflict whose values are all aliases or parenthetical variants is not a conflict.
- **Attribute `source_url` to the provider that supplied the prose**, never to one that merely returned a match — a citation pointing at a page with no biography is worse than no citation.
- Point a provider that fails **per provider**, never collapse it into "no results": a 429 or a timeout must be distinguishable from "this person has no biography".

## Phase 0 — the row-existence gate (before any bio work)

A credit can exist on an item while its **person row** never materialized — nothing can be written to a row that is not there, and a library refresh does not always create it.

- Audit: for every credited name, look it up as `IncludeItemTypes=Person` and record whether a row exists. Names with credits but no row are a separate, blocked list — they need a library-scoped `POST /Items/{libId}/Refresh` (both ReplaceAll flags false) and a re-audit after ~2 min, *before* spending any bio work on them.
- If the refresh still does not create the row, the name is terminal for this pass — report it as a scanner-level gap, not a metadata gap, and do not retry the refresh in a loop (the backlog drains sequentially and re-firing a queued update is a no-op).

## Reviewing sweep state in one view (standing user preference)

**Two different jobs, two different shapes.** *Scanning* many rows wants one dense view; *deciding* on rows wants one item at a time.

- **Scan → one searchable HTML widget** in the chat: dump Person rows (`IncludeItemTypes=Person`, `Fields=Overview,ImageTags,ProviderIds`, paginated) plus per-name credit counts to JSON and render a widget with a search box, a "bio only" toggle, film-count ordering and empty bios dimmed. Gap-spotting becomes a scroll instead of a hundreds-of-clicks audit.
- **Decide → `clarify` clickable options, one person per question, ≤5 per call**, each quoting the current bio text and the credit that makes it suspect, with the recommendation first. Never a markdown table that asks the user to type row codes — they review on a phone and cannot open files or transcribe row numbers.

## Pre-1900 collision audit (same-name wrong-person screening)

A modern library's subjects are contemporary. A biography describing a 19th-century (or earlier) life on a modern credit is almost always a **different person with the same name** — a naval officer, a politician, a disambiguation page.

**Extract the birth year; never match "a pre-1900 year".** Matching any four-digit year is the trap and produces mostly noise:

| Bio | Year a naive scan matches | Reality |
|---|---|---|
| "William James Pullman (born December 17, 1953)…" | `1600` (unrelated text) | modern, correct |
| "…secretary of the treasury from 1789 to 1795…" | `1789` | office date, not birth |
| "William Shakespeare (26 April 1564 – 23 April 1616)" | `1616` | death year |

Patterns that do find real birth years (apply to the opening ~600 chars):

- `\(\s*born\s+(?:\d{1,2}\s+\w+\s+|\w+\s+\d{1,2},\s+)?(\d{4})\)`
- `\bborn\s+(?:on\s+)?(?:\d{1,2}\s+\w+\s+|\w+\s+\d{1,2},\s+)?(\d{4})\b`
- `\((?:c\.\s*)?(?:fl\.\s*[\d\w]+\s*[–-]\s*)?(\d{4})\s*[–—-]`
- `\b(?:b\.|born in)\s+(\d{4})\b`

Handle the forms that defeat a naive parenthetical match: `(c. 1560 – October 1622)` (note `c.` plus a **thin space** U+2009 — `\s*` covers it), `(fl. 1670s – 1723)`, and day-first dates `(26 April 1564 – …)` where the paren opens on a day, not a year. A row the patterns miss is **undetected, not modern** — bucket it separately and read it.

**Then classify before acting.** Pre-1900 births are frequently CORRECT:

- **Source authorship** — the novelist/playwright whose work the film adapts: Leprince de Beaumont on *Beauty and the Beast*, Shakespeare on *Romeo + Juliet*, Dickens on *The Old Curiosity Shop*, Grimm on *Tangled*. Keep.
- **Archive footage subjects** — presidents, statesmen, scientists, painters appearing in documentaries. Keep.
- **Collisions** — a modern crew credit (director, producer, editor, journalist, musician) carrying a 19th-century naval officer, politician, or disambiguation page. Clear or replace.

Diagnostic that separates the two quickly: a 19th-century life whose profession is navy/army/politics on a music or modern-film credit is a collision; an author/painter/statesman whose work or likeness the film uses is legitimate.

**Repair protocol** (the licensed exception to never-clobber):

1. Re-read the row's DTO fresh; **refuse unless the current Overview still contains the flagged year**. A row that no longer matches was already fixed.
2. Either write a replacement that passed the identity gate, or clear to an empty Overview so the row becomes an honest gap — never invent text to fill it.
3. Poll the read-back (0/1/2/3 s) before reporting failure; a clear that reads back stale has usually landed.
4. Journal `{id, name, action, source, old_len, new_len, old_head, new_head}` per repair as the audit trail.

**A wrong bio that survives a refresh is still wrong** — audit by reading the text, never by counting non-empty Overviews.

**Measure on the real distribution, not a hand-picked sample.** A 10-title sample drawn from star-led documentaries measured ~88% fillable; the full gap list sorted by credit count was ~98% crew with no public footprint at all — the sort order decides which class of person you are measuring. Sample the sorted list you will actually run, or the yield you promise is fiction.

## Traps

- **Refresh People auto-attaches ids to hand-built person rows** (an empty same-name shell lands on a researched name). Re-read `ProviderIds` on every hand-built row after the task runs; clear bogus ids — the bio lock holds, so only the ids need repair.
- **Audit snapshots go stale mid-pass** — the People task backfills while a run is in flight, so recount live at the end and let the never-clobber guard absorb the drift.
- **Never run crew gaps through musician-only sources.** AllMusic/Discogs/Last.fm index recording artists, not directors and producers — a crew name through them returns a wrong same-name musician that passes a casual glance. Route Director/Producer/Writer department gaps to film sources (Criterion, IMDb, publisher bios) from the start.
- **Renaming a Person row breaks two things that must be repaired in the same pass.** A spelling fix plus a repin to the true provider id (single-p "Martin Phillips" → the double-p Chills founder) leaves the film's embedded credit carrying the OLD name/id, so the item silently loses that person — 5 credits become 4. Re-read the film's `People` array after the rename and re-add an entry carrying the **person row's** id. The server can also materialise a **duplicate** person row carrying the provider's own bio; that is provider data, not a mistake to silently delete — report it and let the user choose. Prove both with a re-read of the film plus a Person search on the corrected spelling.
- **A locked bio can be locked WRONG.** Presence and length checks report a wrong-life bio as complete; only reading the text (and checking the row's `Imdb` against the `Tmdb` record's `imdb_id`) catches it. When correcting one, change Name+Overview in the same DTO write and keep the locks.
- **A test must never write a real state artefact.** Planning/writing helpers that default to a fixed path (`state/meta-plan.json`) get called by a unit test with fake rows and silently clobber a completed multi-hundred-row plan. Give every plan/write function an injectable output path, have the test pass `tmp_path`, and treat a fixture-shaped file appearing in `state/` as the tell.
- **A provider demoted by ranking is not blocked.** Merge helpers commonly add "providers not named in any priority order still count, ranked last", which silently re-admits a demoted source. A demotion needs a code-level block for the field it must not supply, plus a test that asserts the fallback cannot reintroduce it.
