# netgen license public key

`tlink-public.pem` in this directory is the RSA public key that the
client uses to verify offline license codes issued by
tlink-license-server.

## Where it comes from

The tlink-license-server auto-generates its RSA keypair on first
use. On the server host, the pair lives at:

```
${databasePath}/offline-keys/offline-private.pem   (chmod 600)
${databasePath}/offline-keys/offline-public.pem    (chmod 644)
```

`${databasePath}` is set in `tlink-license-server/src/config.js`;
default is `tlink-license-server/data/`, so the file you want is
usually:

```
tlink-license-server/data/offline-keys/offline-public.pem
```

## How to install

```
cp ~/dev/tlink-license-server/data/offline-keys/offline-public.pem \
   resources/license/tlink-public.pem
```

Then rebuild and reship the netgen wheel. **The public key is
freely distributable — it's fine to commit it to a public repo.**
Never copy the matching `offline-private.pem`.

## Rotation

If you regenerate the server keypair (delete the `offline-keys`
directory and restart the server), every previously-issued offline
code becomes unverifiable — customers will see
"invalid signature" and need a fresh code. Ship a new netgen wheel
with the new public key BEFORE reissuing customer codes so they
have a client that can verify them.

## Override for dev / testing

Set `NETGEN_LICENSE_PUBKEY_PATH=/path/to/pem` in the client's
environment and the module will read that file instead of the
bundled one. Useful when pointing at a staging license server
without rebuilding the wheel.
