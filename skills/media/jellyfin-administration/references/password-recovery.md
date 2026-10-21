# Jellyfin password recovery (PBKDF2-SHA512 reset)

Passwords are one-way PBKDF2-SHA512 hashes — **recovery is impossible**; the only path is
resetting the hash in `jellyfin.db`. This recipe was verified end-to-end on Jellyfin 10.11.11
(CT105 on prox1, unprivileged LXC, docker container).

## Facts established from the live DB + Jellyfin source

- Hash string format (PHC spec, **hex** encoded salt/hash — NOT base64):
  `$PBKDF2-SHA512$iterations=210000$<salt_hex_upper>$<hash_hex_upper>`
  - salt = 16 bytes (32 hex chars), derived key = 64 bytes (128 hex chars)
  - iterations: 210000 default (Constants.cs `DefaultIterations`); older accounts may be 120000 —
    verification reads iterations from the string, so any value works as long as hash matches.
- Users table (10.11 schema): `Username`, `Password`, `AuthenticationProviderId`,
  `PasswordResetProviderId`, `MustUpdatePassword`, `EnableAutoLogin`, `InternalId`.
  No `IsDisabled` column.
- Admin determination: `Permissions` table rows `(UserId, Kind, Value)` — **Kind=0 with Value=1 =
  IsAdministrator**. On this install: `root` and `remote` are admins; `tv`, `emily`, `kathleen` are not.
- Jellyfin source files backing the format:
  `Emby.Server.Implementations/Cryptography/CryptographyProvider.cs`,
  `MediaBrowser.Model/Cryptography/PasswordHash.cs`, `MediaBrowser.Model/Cryptography/Constants.cs`.

## Reset procedure

All commands run on the prox1 host (root), reaching into the LXC's docker:

```bash
# 1. stop the container so it can't overwrite the DB change
pct exec 105 -- docker stop jellyfin

# 2. back up the DB
D=/var/lib/lxc/105/rootfs/opt/jellyfin/config/data
cp $D/jellyfin.db $D/jellyfin.db.pwreset.bak

# 3. generate the hash and update the row (run on the prox1 host — python3 present)
python3 - <<'PYEOF'
import sqlite3, hashlib, os
pw = "REPLACE_WITH_NEW_PASSWORD"
salt = os.urandom(16)
h = hashlib.pbkdf2_hmac("sha512", pw.encode(), salt, 210000, dklen=64)
hash_str = f"$PBKDF2-SHA512$iterations=210000${salt.hex().upper()}${h.hex().upper()}"
db = "/var/lib/lxc/105/rootfs/opt/jellyfin/config/data/jellyfin.db"
c = sqlite3.connect(db)
c.execute("UPDATE Users SET Password=? WHERE Username='root'", (hash_str,))
c.commit(); c.close()
PYEOF

# 4. start the container
pct exec 105 -- docker start jellyfin
```

## Verifying the reset via the API

The `X-Emby-Authorization` header is **mandatory** — without it Jellyfin 10.11 throws a bare
stack trace (`ExceptionMiddleware ... AuthenticateNewSessionInternal`) that looks like a server
bug. With it you get a clean JSON success (full User object) or a clean "invalid username or
password".

```bash
curl -s -X POST http://127.0.0.1:8096/Users/AuthenticateByName \
  -H "Content-Type: application/json" \
  -H 'X-Emby-Authorization: MediaBrowser Client="HermesTest", Device="CT105", DeviceId="hermes-test-001", Version="1.0.0"' \
  -d '{"Username":"root","Pw":"REPLACE_WITH_NEW_PASSWORD"}'
```

Success signature: `{"User":{"Name":"root",..."HasPassword":true...}}`.

## Notes

- Failed-login attempts appear in the log as `Authentication request for "<name>" has been denied
  (IP: ...)` — a quick way to see what username the user is trying before you tell them the real list.
- After first login, reset the other accounts via Dashboard → Users (no DB surgery needed) and
  tell the user to change the recovery password (it was set by recovery).
- Keep the generated password out of logs/scripts; write it to a root-only tmp file and tell the user.
