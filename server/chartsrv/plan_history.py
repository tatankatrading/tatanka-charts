"""Each symbol's If/Then block, kept version by version with the time it was saved: what a plan said before a session,
however it was edited after. The settings key ifthen_history ({symbol: [{"rows", "saved"}, ...]}, oldest first, the
newest KEEP per symbol), so a backup carries it with the blocks themselves (settings key "ifthen").

Both writers of a block record into it: PUT /api/ifthen/{symbol} (the Plan tab) and the Indicators dialog's RowsBlock
(indicators.IndicatorSettings.put_symbol). Each calls remember() before it writes the new block, so the block being
replaced is stored first, at its own save time, when the history doesn't have it yet: a plan saved before the history
existed still counts from when it was saved, saved again unchanged or not. Saving the same text again adds nothing.

GET /api/ifthen/{symbol}/history?until= returns the versions saved by then (versions()), the current block too when
the history has never seen it."""
SETTING = "ifthen_history"
KEEP = 90


def _list(store, symbol: str) -> list[dict]:
    hist = store.get_setting(SETTING, {}) or {}
    out = hist.get(symbol) if isinstance(hist, dict) else None
    return [v for v in out or [] if isinstance(v, dict) and isinstance(v.get("rows"), str) and isinstance(v.get("saved"), int)]


def _current(store, symbol: str) -> dict | None:
    """The symbol's saved block as a version ({rows, saved}), or None (none saved, or saved with no time)."""
    block = (store.get_setting("ifthen", {}) or {}).get(symbol) or {}
    rows, saved = block.get("rows"), block.get("updated")
    if not isinstance(rows, str) or not isinstance(saved, int) or saved <= 0:
        return None
    return {"rows": rows, "saved": saved}


def remember(store, symbol: str, rows: str, now_ms: int) -> bool:
    """Records `rows` as the symbol's newest version, saved at now_ms; called before the new block is written. The
    block it replaces goes in first, at its own save time, when no version holds its text (a plan saved before this
    history began), also when `rows` is that same text (Save pressed again, the Indicators dialog's OK): then it alone
    is stored, so the plan keeps counting from when it was first saved. Nothing else is added when the newest version
    already holds `rows`. True when a version of `rows` was added at now_ms."""
    hist = store.get_setting(SETTING, {}) or {}
    if not isinstance(hist, dict):
        hist = {}
    versions = _list(store, symbol)
    old, added = _current(store, symbol), False
    if old and not any(v["rows"] == old["rows"] for v in versions):
        versions.append(old)
        versions.sort(key=lambda v: v["saved"])
        added = True
    if versions and versions[-1]["rows"] == rows:
        if added:   # only the replaced block was new (or the same text saved again: its first save kept)
            hist[symbol] = versions[-KEEP:]
            store.set_setting(SETTING, hist)
        return False
    saved = max(now_ms, versions[-1]["saved"] + 1) if versions else now_ms   # strictly after the one before
    versions.append({"rows": rows, "saved": saved})
    hist[symbol] = versions[-KEEP:]
    store.set_setting(SETTING, hist)
    return True


def versions(store, symbol: str, until: int | None = None) -> list[dict]:
    """The symbol's versions saved by `until` (epoch ms; None: all), oldest first. The current block is the newest when
    the history doesn't hold its text (saved before the history began, and not since)."""
    out = _list(store, symbol)
    cur = _current(store, symbol)
    if cur and not any(v["rows"] == cur["rows"] for v in out):
        out.append(cur)
        out.sort(key=lambda v: v["saved"])
    if until is not None:
        out = [v for v in out if v["saved"] <= until]
    return out
