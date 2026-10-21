# Usenet downloader setup (SABnzbd on CT101)

SABnzbd lives on the fetch side with the other downloaders: CT101
`media-download`, Docker, alongside qBittorrent and Prowlarr. Serve role
stays on CT105 jellyfin.

## Deploy

Linuxserver image, mirroring the qBittorrent container's mounts and ids.
Host port must not collide (qBittorrent already maps host 8090; Prowlarr
9696) — host 8095 → container 8080 is the standing choice:

```
docker run -d --name sabnzbd --restart unless-stopped \
  -e PUID=1000 -e PGID=1000 -e TZ=Pacific/Auckland \
  -p 8095:8080 \
  -v /opt/sabnzbd/config:/config \
  -v /mnt/downloads-fujh:/downloads \
  -v /mnt/zmedia/downloads:/downloads-zmedia \
  ghcr.io/linuxserver/sabnzbd:latest
```

Config lives at `/opt/sabnzbd/config/sabnzbd.ini`; the API key is the
`api_key` line. Credentials (API key, provider login, indexer key) go in
`.env`, never in chat or compose files.

## Configure via the API (no UI clicks)

```
BASE=http://192.168.214.11:8095/api ; KEY=<api_key>
# working dirs on the WRITABLE share (same rule as qB: /downloads-fujh is read-only)
curl "$BASE?mode=set_config&section=misc&keyword=download_dir&value=/downloads-zmedia/sabnzbd/incomplete&apikey=$KEY"
curl "$BASE?mode=set_config&section=misc&keyword=complete_dir&value=/downloads-zmedia/sabnzbd/complete&apikey=$KEY"
# provider server slot (block account: no expiry pressure for gap-filling)
curl "$BASE?mode=set_config&section=servers&name=<label>&host=<host>&port=563&ssl=1&username=<u>&password=<p>&connections=20&timeout=60&enable=1&apikey=$KEY"
```

SABnzbd has no `test_server` API mode (`{"status":false,"error":"not
implemented"}`) — prove the server yourself instead.

## Verify the provider before trusting it

1. **Resolve the hostname FROM CT101, not from the agent sandbox.**
   Provider endpoint names die silently — a retired name returns NXDOMAIN
   even via public DNS while everything else resolves fine, which reads
   as a credentials failure if you skip this step. `getent hosts <host>`
   from `pct exec 101`; on NXDOMAIN switch to the provider's working
   regional endpoint (same account, same block) and re-verify.
2. **TLS + NNTP auth from CT101.** Pipe `AUTHINFO USER` / `AUTHINFO PASS`
   / `QUIT` into `openssl s_client -connect <host>:563 -quiet` and
   require `281 Authentication accepted` (plus a sane cert CN). This is
   the whole proof — a 204/empty warnings list from SABnzbd only means it
   saved the config, not that login works.
3. `GET $BASE?mode=warnings&output=json` should stay empty after the
   server goes live; anything about authentication there is a real login
   failure, not a config typo.

## Indexer side

Usenet indexers (NZBgeek and kin) attach to Prowlarr exactly like torrent
indexers — same schema-driven `POST /api/v1/indexer` body (priority,
trailing-slash `baseUrl`, `appProfileId`, `redirect: true` for
`protocol: usenet`) documented in the parent skill. Verify the indexer
API key directly (`t=caps`, then `t=search`) before adding: a trial-tier
key passes every local check and fails only at Prowlarr's create-time
validation.
