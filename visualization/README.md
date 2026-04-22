# Visualization

This directory contains the first no-build custom visualization for `aw-watcher-llm`.

Current layout:

- `dist/index.html`
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
