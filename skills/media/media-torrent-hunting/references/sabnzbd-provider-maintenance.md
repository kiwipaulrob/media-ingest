# SABnzbd provider maintenance (servers, credentials, liveness)

How to prove a Usenet provider is actually working, and how to fix it when it is not.
Reach for this the moment a grab fails with `Repair failed, N short` or `Aborted, cannot be
completed`, BEFORE concluding the post is incomplete.

## Why this matters

SAB fails over to the next server silently. A provider whose login is broken contributes
nothing and produces **no visible error in the queue or history** — the job simply behaves
exactly like a title whose articles are missing from the one working backbone. Diagnosing
that as "incomplete at the provider" sends you to buy a new block account for a problem a
password change fixes.

## 1. Prove which server actually serves traffic

```python
from media_lib import sab
d = sab._call('server_stats')['servers']
for name, v in d.items():
    print(name, 'lifetime', v['total'], 'month', v['month'], 'day', v['day'])
```

Interpretation:

- One server at ~100% of lifetime traffic and another at a few MB **ever** = the second is
  not working. A real fill server shows a steady minority share.
- Take `day` deltas around a single grab. `farm +1,056` against `block +2,607,787,542` during
  a 3.5 GB job proves the farm was never used; both numbers are needed because a large job
  makes tiny deltas look like noise until you compare them.

## 2. Test the login at the protocol level (bypasses SAB)

Open a raw NNTP session and read the response codes. Mask credentials in all output.

```python
import socket, ssl
s = ssl.create_default_context().wrap_socket(
    socket.create_connection((host, 563), timeout=25), server_hostname=host)
f = s.makefile('rwb')
f.readline()                                  # 200 greeting
f.write(b'AUTHINFO USER <user>\r\n'); f.flush(); f.readline()   # 381 Need more.
f.write(b'AUTHINFO PASS <pass>\r\n'); f.flush(); print(f.readline())  # 281 = accepted
```

- `281 Authentication accepted` — credentials fine; the problem is elsewhere (usage,
  priority, or the provider genuinely lacking the articles).
- `482 Invalid username or password` — the stored credential is stale. Fix it (section 4).
- Connection refused/timeout — host unreachable, a different problem entirely.

Read the credentials from SAB's own config for this test, so you are testing
exactly what the client uses — and print only response codes, never values.

## 3. SAB's own log is the tie-breaker

```bash
ssh <sabhost> "grep -iE 'Connecting .*@<host>|no such article|too many connections' \
  /opt/sabnzbd/config/logs/sabnzbd.log | tail -20"
```

`Connecting 1..20@news.<provider> finished` means SAB opened all its connections — the
login is live and SAB IS asking that server. Combined with a near-zero byte delta, this is
the definitive "authenticated but has nothing for me" state, which is a *post* problem, not
a *provider* problem.

## 4. Repairing credentials: edit the ini, not the API

`set_config` does **not** persist the servers section. Tested shapes that all silently
return a template row and change nothing: bare array, `{"servers": [...]}` wrapped, each
with and without `keyword`, over GET query params and over a POST form body. Worse, some of
them **append junk server rows** (named for the keyword, empty `host`).

The reliable route:

```bash
# 1. find the config (docker bind mount, not inside the container)
ssh <sabhost> "docker inspect sabnzbd --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'"
# e.g. /opt/sabnzbd/config -> /config

# 2. stop FIRST — SAB rewrites its ini on shutdown, so a live edit is overwritten
ssh <sabhost> "docker stop sabnzbd && cp /opt/sabnzbd/config/sabnzbd.ini /opt/sabnzbd/config/sabnzbd.ini.bak-$(date +%Y%m%d-%H%M%S)"
```

Edit only the target block: the ini shape is

```ini
[servers]
[[BlockNews]]
  username = ...
  password = ...
  host = eunews.blocknews.net
[[usenetfarm]]
  username = ...
  password = ...
  host = news.usenet.farm
```

Then start the container and re-read the file to confirm the values took. Pipe credentials
in on stdin (a `/tmp` file mode 600 that you delete afterwards) rather than putting them in
an ssh command line where they land in `ps` and shell history. A matching `get_config`
response proves nothing on its own — SAB can echo masked or stale values.

## 5. Cleaning up junk rows

Rows created by the failed API writes have `host = ""` — note the **quoted empty string**.
A truthiness check treats `""` as non-empty and keeps them, so strip quotes before deciding:

```python
host_clean = host.strip('"') if host else ""
```

Remove them only from the `[servers]` section; the same `[[name]]` syntax is used by
`[categories]` (`[[*]]`, `[[movies]]`, `[[tv]]` …), so a naive "every `[[x]]` is a server"
parse will report categories as hostless servers.

## 6. Verdict table

| server_stats | NNTP auth | SAB log | Verdict |
|---|---|---|---|
| one server ~100% | — | — | second provider unused — investigate before judging any post |
| second ~0 bytes | `482` | no connects | stale credential — fix the ini |
| second ~0 bytes | `281` | `Connecting 1..20@host finished` | authenticated, has no more articles — genuine post gap, stop grabbing |
| second serving real bytes | `281` | connects + transfers | healthy fill server |
