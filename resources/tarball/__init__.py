"""Tarball-install helper scripts bundled inside the wheel.

v0.5.49: these scripts traditionally lived only in
`scripts/tarball/` (outside the wheel) and were installed to
`/opt/netgen-server/bin/` by the tarball installer at server
install time. That created a self-update gap: improvements
shipped in the wheel never reached the installed scripts.

Now they're ALSO bundled here as package data. At server
startup `_ensure_netgen_upgrade_script_deployed()` (in
run_tgen_server.py) copies the bundled version into
`/opt/netgen-server/bin/netgen-upgrade` when it differs from
what's already there, so the next wheel upgrade picks up the
latest fixes.

Keep `scripts/tarball/netgen-upgrade` and
`resources/tarball/netgen-upgrade` byte-identical — a regression
test pins this.
"""
