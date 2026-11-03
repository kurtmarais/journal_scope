# Working notes for Claude

- Develop on `claude/upbeat-cerf-mntinq`, then always open a pull request into `main` after pushing changes,
  even when Cloudflare deployments appear to build the work branch. The phone app is deployed from `main`.
- When a change touches the phone app, bump `APP_VERSION` in `web/app.js` and the cache name in `web/sw.js`.
- Run `py -m pytest` (or `.venv/bin/python -m pytest`) before pushing.
