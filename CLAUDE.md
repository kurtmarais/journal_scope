# Working notes for Claude

- Develop on `claude/upbeat-cerf-mntinq` and push there. Do not open pull requests into `main` unless asked:
  Cloudflare deploys the phone app from `claude/upbeat-cerf-mntinq`.
- When a change touches the phone app, bump `APP_VERSION` in `web/app.js` and the cache name in `web/sw.js`.
- Run `py -m pytest` (or `.venv/bin/python -m pytest`) before pushing.
