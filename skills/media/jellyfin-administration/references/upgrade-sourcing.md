# Sourcing higher-resolution replacements

Taking the low-resolution candidate list from `references/library-quality-sweep.md` and finding a higher-resolution copy of each title — then reporting it honestly. The sweep says *what is too low*; this is the other half, *what actually replaces it*.

## Expect the null to dominate, and say why

Most "no upgrade exists" answers are structural, not a failed search. Naming the mechanism is what makes a null result credible instead of lazy:

- **Public broadcasters were SD-only for a long era.** BBC Four did not broadcast in HD until ~2013, so a whole generation of music documentaries only ever circulated as PDTV/XviD captures. There is no HD master to find — the SD rip **is** the release.
- **A series' HD run often starts late.** Where HD releases begin at a much later season, every earlier season genuinely has no HD copy in existence. Check the series' HD start point before hunting season-by-season.
- **Some series ship DVD-only.** When the complete series exists as DVD only, DVD *is* the ceiling — a 576p DVD rip is not an upgrade over a 480p one, so it fails the bar.
- **Silent/absent titles are common.** Off-air regional or single-broadcast programmes frequently have no torrent presence at all anywhere.

Deliver a triage: upgrades found, upgrades not found **with the structural reason per class of title**. A bare list of wins hides that most of the tier was checked and legitimately could not improve.

## Workflow (fan out, then aggregate from files)

1. Export the candidate list to JSON (`title`, `year`, `current_h`).
2. Chunk it to `chunk_NN.json`, ~25–30 titles per chunk — one chunk per subagent, one subagent per chunk.
3. Give every subagent the **same fixed output schema** and its own output path, so results land as files rather than as prose:
   `title, year, current_h, improvement_found (bool), best_resolution, source_name, source_url_or_magnet, indexer, notes`
4. Each subagent writes `sweep_results/chunk_NN.json` and returns a summary of **only the positives** plus a count of nulls.
5. **Aggregate from the files, never from the summaries.** Count the files, count the rows, and assert `rows == expected titles` and `upgrades + nulls == total`. Summaries are self-reports and can silently disagree with the file — the file is the artifact.
6. Ship three outputs: a summary table in chat, the CSV/JSON, and a browsable widget. The user asks for a *report*, so leave durable artifacts on disk, not just a chat message.
7. **Namespace every subagent's scratch files.** Parallel children share one working directory, so a
   generic `harvest.py` / `build_results.py` / `run.py` gets overwritten mid-run by a sibling and
   silently breaks that sibling's work with no error surfacing to the parent. Put the chunk id in
   every script and raw-evidence filename (`c07_harvest.py`, `raw_chunk07.json`), and tell the
   children to do the same.
8. **Diversify or stagger indexers across chunks.** Free indexer APIs enforce per-day quotas that
   parallel chunks *share*, so later chunks start getting `429`s and record them as nulls — a false
   negative indistinguishable in the aggregate from a genuine "no upgrade exists". Assign different
   indexers per chunk, space the requests, and treat a chunk that abruptly returns all-null as
   suspicious rather than as data.

## Searching for a release

### Which indexers actually work

Observed across one large parallel sweep — note how *few* of these are absolute, which is the point:

| Source | Behaviour |
|---|---|
| LimeTorrents | Best hit rate. Detail pages carry a **full real magnet** in the HTML. |
| Bitsearch | Resolution inline in titles; detail page has the infohash (its `/download/torrent/<40-HEX>` URL path carries it too). |
| TorrentDownloads / TorrentFunk | Good for ITV/BBC one-off documentaries when the above are empty. |
| TheRarBG | Search page 302s to the homepage under `curl`, but the JSON endpoint `/get-posts/keywords:<q>:format:json/` returns results; detail pages yield the magnet in a **browser**. |
| TorrentClaw | The *search* page is a JS shell with nav text only — useless. Detail pages reached from a search result do carry magnets. |
| SolidTorrents JSON API | Works until its free tier (~200 req/day) is exhausted — and that quota is **shared across parallel agents**, so it starts 429ing mid-run. |
| The Pirate Bay / apibay | Intermittent: sometimes result rows, sometimes the generic "recent torrents" page for *every* query. A refusal, not an empty index. |
| 1337x, EXT Torrents, bt4g, Kickass, DocuWiki | Cloudflare-blocked to extractor and headless browser alike. Don't retry. |

**Treat every verdict in that table as per-run, not permanent.** A source that refuses one fetcher, or hits a shared quota, is not a dead source. Writing one run's failure down as "do not bother" is precisely how a whole class of titles gets reported as "no upgrade exists" when the sources were never really queried — and a chunk that abruptly returns all-null is more likely throttled than empty.

- **Hit indexer endpoints and mirrors directly.** General web search tends to surface streaming aggregators and fan uploads instead of releases; the torrent indexers and meta-search endpoints (plus DHT-metadata mirrors) return actual release names, sizes and magnets.
- **A null from one indexer is not evidence.** Sites refuse fetchers intermittently. When one refuses, fall through to another mirror or meta-search before recording "not found" — a single site's refusal silently becomes a global false negative in the report.
- **Record only what you actually saw.** A release named in a sidebar, a search snippet, or a page you could not fetch is a *lead*, not a find — mark it as such. Never let a plausible-looking release name enter the report as a confirmed source.
- Prefer magnets/pages observed in a fetched response; flag download-only findings (blog/DDL posts) as such since they need different tooling.

### Ask the local Prowlarr FIRST — it is the built-in route

Before hand-scraping any public indexer, query the homelab's Prowlarr (`http://192.168.214.11:9696`,
key in `/opt/prowlarr/config/config.xml`) — it already fronts ~13 indexers, sorts by seeders, and
returns a `downloadUrl` per hit. Two mechanics make it work:

- **The `downloadUrl` answers with an HTTP 301 whose `Location` header IS the magnet.** Default
  urlopen follows the redirect and dies on the `magnet:` scheme (`Redirection to url 'magnet:...' is
  not allowed`). Install a redirect handler that raises `HTTPRedirectHandler.redirect_request`, catch
  the `HTTPError`, and read `e.headers['Location']` — that string is what goes to `torrents/add`.
  Same technique works for any `/download` proxy that fronts a magnet.
- **Always append `&dn=<readable name>` when you build the magnet yourself.** A magnet with only
  `xt=urn:btih:<hash>` gives a qBittorrent row whose display name is the bare hash until metadata
  arrives — the client then looks like it never accepted the add. qBittorrent returns **409 Conflict**
  when an infohash is already present, which is proof of a duplicate rather than a failure; do not
  retry it.

Query with the release name plus the distinguishing token you want (`<title> <year> SARTRE`,
`<title> WAC`, `<title> <year> <resolution>`), then filter the returned titles for those tokens and
pick the highest seeder count — a Prowlarr search is cheap, so run one per title rather than
hand-building index URLs.

### Recovering a magnet the page hides

- **A detail page that yields no magnet is usually JS-rendered, not magnet-free.** Plain extraction
  returns the HTML shell while the magnet is injected client-side, so the regex finds nothing and the
  release looks like a lead with no link. Load the page in a real browser and regex the magnet out of
  the **rendered** DOM (`document.documentElement.innerHTML`) — recovering several magnets per batch
  this way is routine. Treating an empty extract as "no link" silently discards real sources.
- **Unescape HTML entities in any magnet lifted out of page markup.** Harvested magnet URIs come back
  with `&amp;` where `&` belongs, so pasting one straight into `torrents/add` truncates it at the first
  tracker parameter. Run it through `html.unescape` first.
- **Check the URL for a bare infohash before fetching at all — but only a real 40-hex one.** Several
  indexers put the hash directly in the path (`/download/torrent/<40-HEX>`, `/torrent/<40-HEX>.torrent`),
  so the magnet can be built straight from a URL already in hand, with no page fetch needed. Not every
  hash-like path segment is an infohash, though: some indexers use a shorter internal id (Bitsearch's
  plain `/torrent/<24-HEX>` form). Treating that id as a hash builds a magnet for a release that does
  not exist, and it fails silently. Match `[0-9a-fA-F]{40}` exactly — if nothing matches, fetch the page.
- **Never build a magnet from an infohash that merely appears somewhere on the page.** Sidebars,
  "related" blocks and recommendation widgets carry *other* releases' hashes, so an unverified hash
  downloads a completely different work. Only use a hash you can tie to the specific release name.

### Stream-only sources: assess the DRM before attempting a download

- **Read the manifest URL before reaching for yt-dlp.** Play the title in a real browser, then pull
  the player's current source / network manifest: URL tokens like `bccenc`, `cenc` or `fairplay`
  mean Brightcove-style CENC/FairPlay encryption, and yt-dlp will refuse those formats outright.
  One manifest read settles obtainability in seconds; a download attempt teaches nothing more.
- **A site login does not change CDN-level encryption.** An authenticated session unlocks catalog
  features (watchlists, rentals) but serves the identical encrypted manifest — if the anonymous
  manifest is DRM, do not spend a login working around it.
- **Grade DRM-locked full-length streams as Soft with the mechanism named** (title present, parts
  and runtimes confirmed on the catalogue page, segments encrypted), and move on to the next source
  rather than re-proving the refusal per part.

## Grade every candidate before recommending a download

- **Hard** — real magnet/URL with trackers observed and a live swarm. Download-ready.
- **Soft** — 0 seeders, a streaming-only upload, or a resolution asserted only by the upload title. Still worth listing, but flag it: the swarm may never move.
- **Different programme** — the release is not the work sitting in the library. Two forms, both
  common: a making-of or featurette living inside another title's disc as an *extra* (extras are
  frequently upscaled SD), and a **namesake or remake** — a later film or series sharing the exact
  title, and often the year. Match against the **held file's own identity** (its path, runtime,
  subject), never against title+year alone: that shortcut has put a feature film against a 3-minute
  short and a 2020s series against an identically-titled 2010 two-parter, both of which read as clean
  wins until the library path was checked. Say so rather than counting it as a win.
- **Master exists, no copy** — a Blu-ray/disc release is confirmed to exist but no obtainable rip was found. This is a "source manually" item, not an automated download.

## Present the shortlist so the user can select by number

The report exists so the user can choose what to download, so make choosing mechanical:

- **Number every candidate continuously and keep that numbering stable** across the chat table, the
  markdown file and the CSV. The user replies with numbers ("skip 36, 37, 45; add the rest"), so a
  list that gets re-sorted or renumbered between messages makes their selection meaningless.
- **Group by resolution band, highest first, and sort by size of jump within a band.** That ordering
  *is* the priority; do not hand the user an undifferentiated 100-row list.
- **Put the exceptions in a flag column, not in prose:** best-master (`BluRay`), `verify` (resolution
  unstated, or a different edit of the same work), and `WRONG-MATCH` (a different programme entirely).
  The user then excludes a whole class by number instead of reading every note.
- **State the unique-download count next to the row count.** Rows routinely collapse onto one download
  — a per-episode set that is really one season pack, or one film matched against three library files.
  "97 selected" is 88 fetches: give both numbers and name which rows merge, or the batch reads as more
  work than it is and the user plans around a wrong figure.
- **Carry the link and the source release name into the CSV** so the later add step never re-searches.

## Verify before replacing anything

- **An "upgrade" label is a claim, not a fact.** Re-probe the downloaded file's real resolution before deleting the incumbent — releases advertised at one resolution have turned out to be far lower.
- Run the full ingest gate on the replacement (extension equals the real container, mid-file decode probe, 4/4 metadata) and install it with the in-place swap recipe in `references/ingest-and-intake.md` so the existing row, provider IDs and poster survive.
- Keep the superseded file until the replacement is confirmed live in Jellyfin.
