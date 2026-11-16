## What it changes

<!-- What and why, in a few lines. Link the issue it fixes ("Fixes #12"). -->

## How it was tested

<!-- What you ran and what you checked by hand in the app (desktop and phone, if it touches the page). -->

- [ ] `cd server && python -m pytest -q` passes
- [ ] `ruff check --select E9,F63,F7,F82 server` is clean
- [ ] `for f in web/*.js; do node --check "$f"; done` is clean
- [ ] Docs updated (README.md or docs/), if what users see or set up changed
- [ ] No keys, tokens, webhook URLs, account numbers or personal paths in the code, docs or tests
