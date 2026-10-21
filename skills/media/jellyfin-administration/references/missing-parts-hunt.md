# Missing-parts hunt (multipart gaps in episodic libraries)

Finding genuinely absent episodes/parts of multi-part works, then sourcing them via Prowlarr + qBittorrent.

## 1. Inventory: group by stem, flag gaps

Walk the video files and group by normalized series stem using part/episode patterns: `part N`, `NofM`, `pt N`, `SxxEyy`, `ep N`, trailing bare number. Flag groups with gaps in the number span or truncated `NofM` totals. Also flag lone singletons matching a part pattern with number 1 (`part 1`, `1of2`) — grouping alone can never see their absent siblings, so confirm the work's real total externally (TMDB) before calling the set complete.

## 2. Guards — most flagged "gaps" are false positives

- Group by (stem, season), never stem alone: S00 specials reuse S01 episode numbers, which invents gaps out of complete seasons.
- Same-folder multi-file sets are legitimately parts of one work, not duplicates — skip groups confined to one folder.
- `NofM` totals baked into filenames can lie; confirm the real episode count against TMDB before hunting.
- Never call a multipart title "missing parts" from a path listing against a stacked item: compare `PartCount` to the folder's file count first.

## 3. Source each missing piece

- Prowlarr search per missing piece: require the identifying tokens, then score-bonus titles containing the missing number (`part 2`, `e02`) or `complete`/`season` packs — a full-series pack fills the gap too. Confirm the winning title actually names the missing piece before adding: a score bonus is a preference, not a proof, and a wrong pick queued under the right label still downloads the wrong file.
- **Search the bare series title first, then per-episode.** Queries built from an episode's guest/contributor names return ZERO candidates from every indexer while a plain `<series title>` query against the same indexers returns the COMPLETE season pack — a full set of per-episode NOMATCH results is evidence about your query, not about availability. Run the broad query before concluding anything is missing, and prefer a season pack over a dozen singles when one exists: packs beat singles at every level, and the pack's availability also tells you the singles are not worth hunting.
- **Before adding, check whether an already-COMPLETE copy of the title is sitting in the queue.** A downloader queued for a title the library already holds — or a second release of it already at 100% under a different name — is a duplicate, not an upgrade. Compare the two releases on resolution, codec and size (and against the held copy) before letting either finish; kill the redundant one with its partial payload rather than letting it consume a download slot.
- Require seeders > 0 and a magnet link — but accept the magnet from EITHER `magnetUrl` or `guid`: some indexers put the raw magnet in `guid` while `magnetUrl` is a Prowlarr redirect link, so filtering on `magnetUrl` alone silently drops every result and reports a false NOMATCH.
- **A `downloadUrl` that will not fetch is usually a 301 to a magnet, not a dead result.** Those indexers serve the `.torrent` fetch as `301 Location: magnet:?xt=...`, and a default opener refuses the hop (`Redirection to url 'magnet:...' is not allowed`), so every result reads as magnet-less. Install a redirect handler whose `redirect_request` raises, catch the `HTTPError`, and read `Location` — that string IS the magnet, ready to hand to `torrents/add`. A second call on the same URL returns `409 Conflict`, which is the queue confirming you already added it, not a failure.
- Prowlarr searches time out intermittently and result sets vary between identical queries minutes apart: retry with alternate queries before recording NOMATCH, and add a good result promptly rather than assuming it will reappear.
- Dedupe by infohash against the qBittorrent queue before adding; verify the hash appears in the queue ~45 s later; record added/nomatch/failed as JSON. Remove a mis-added torrent immediately while it is still at 0% (`deleteFiles=false`).

## 3b. Usenet leg (when torrent swarms are dead)

- A zero from one Usenet indexer is not a miss until a control query passes: search a popular title alongside and require hits there, or the null proves nothing about the content.
- Narrow multi-token queries over-match-AND on some Newznab indexes and return zero while a broad title search surfaces the episodes — always run the broad title query first, then targeted ones, and read the FULL result list (not a capped head) before declaring episodes absent.
- Trial/limited indexer API keys fail validation at add time (`Trial Account Only`) and search with `error 101` — a key that authenticates is not a key that can search; full membership is required for API use.
- Full setup recipe (SABnzbd deploy, provider blocks, Prowlarr Newznab wiring): `references/usenet-setup.md`.

## 4. Scope

Scattered long-running series with dozens of holes across seasons are a completion project, not a gap fill — report the shape and ask before hunting 20+ episodes.
