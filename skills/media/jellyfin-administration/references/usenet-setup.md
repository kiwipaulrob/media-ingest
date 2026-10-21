# Usenet setup (SABnzbd + provider block + Prowlarr Newznab)

Gap-filler pipeline for content dead on torrents: block account (no subscription) + one paid indexer + SABnzbd feeding the same ingest flow as torrent downloads.

## Shape

- **Provider: block, not unlimited**, for sporadic gap-filling — blocks never expire. One block on an Omicron/Highwinds backbone (BlockNews) first; add a second backbone (Usenet.Farm block) only if completion gaps appear. Old posts need 5000+ day retention.
- **Indexer: one paid Newznab index** (NZBgeek) — API search needs full membership; trial keys fail Prowlarr validation and answer `error 101` on direct search.
- **Downloader: SABnzbd**, linuxserver image on the download host beside qBittorrent/Prowlarr: same bind mounts (`/downloads-zmedia` writable share for incomplete/complete — never the read-only mount root), `PUID/PGID 1000:1000`, host port mapped off 8080 (e.g. `8095:8080`). Working dirs via API `mode=set_config&section=misc&keyword=download_dir|complete_dir`. API key lives in `sabnzbd.ini` (`^api_key`); store it in `.env`, never in chat.
- **Automation:** indexer into Prowlarr (below), SABnzbd as Prowlarr download client, series via Sonarr with exact SxxEyy targets.

## Provider hostname pitfall

- The canonical hostname may be dead in DNS while alternates live: `news.blocknews.net` returns No answer even via 8.8.8.8, but `eunews.blocknews.net` (IPv6-only) connects with a valid cert. When a provider hostname fails DNS from the download host, try its EU/US alternates and check IPv6 before concluding the account is bad.
- Verify auth at NNTP level, not via SABnzbd UI: `printf 'AUTHINFO USER u\r\nAUTHINFO PASS p\r\nQUIT\r\n' | openssl s_client -connect <host>:563 -quiet` — `281 Authentication accepted` is the proof. SABnzbd has no working `test_server` API mode.

## Prowlarr Newznab add recipe (POST /api/v1/indexer, from /indexer/schema entry)

Required beyond the schema fields: `name`, `definitionName` (e.g. `NZBgeek`), `protocol: usenet`, `priority` 1–50, `appProfileId` > 0 (list `/api/v1/appprofile`; `Standard` is id 1), `redirect: true` (usenet indexers reject false), `baseUrl` WITH trailing slash, `apiKey`. Success is 201; the create-time connection test is the gate — a 400 names the failing property, fix that, not curl.

## SABnzbd as Prowlarr download client (POST /api/v1/downloadclient, from schema)

Implementation `Sabnzbd`: `host` = container-reachable address, `port`, `apiKey`, `category`. Same validation-gate pattern as indexers.
