"""Developer-only helpers.

Code in this package runs against the local dev stack to keep the
demo and admin app presentable across restarts. Nothing in here ships
to production.

The entry points operators use most:

- :func:`seed.seed_realistic_state` writes a curated set of LEAs +
  syncs + quarantine + cursor rows so the admin app has a "lived-in"
  surface to walk through.
- Reset scripts at ``scripts/reset-*.sh`` wipe one flow's
  mutations (the demo LEA, the quarantine queue, the synthetic
  stale-cursor LEA) without touching the rest of the seeded state.
"""
