# Visualization

This directory contains the first no-build custom visualization for `aw-watcher-llm`.

Current layout:

- `dist/index.html`
- `dist/standalone.html`
- `dist/styles.css`
- `dist/app.js`

It is intentionally shipped as static files so it can be mounted directly through
ActivityWatch `custom_static` without introducing a frontend toolchain yet.

Mount example:

```toml
[custom_static]
aw-watcher-llm = "/Users/rqdmap/Codes/aw-watcher-llm/visualization/dist"
```

Then open ActivityWatch:

1. `Activity`
2. `Edit view`
3. `Add visualization`
4. `Custom visualization`
5. enter `aw-watcher-llm`

The visual reads bucket events from the same server via the REST API.
It now derives both the focus ribbon and root lanes from raw events only.

If you want a full-width page instead of the `Activity` grid tile, open the
standalone page from the same mounted static bundle. It uses the same raw bucket
data, but adds an `AW URL` control so it can also be used outside the grid.

For local use outside ActivityWatch, prefer:

```bash
python3 -m aw_watcher_llm visualize-serve --aw-url http://127.0.0.1:5600
```

This serves the standalone page locally and proxies `/api/...` back to the
configured ActivityWatch server, avoiding `file://` cross-origin restrictions.
