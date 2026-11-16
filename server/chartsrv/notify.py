"""Alert delivery: a private Discord webhook, ntfy phone push, and the app's own notifications (webpush.py). URLs are
never logged."""
import asyncio
import json
import logging
import time

import httpx

log = logging.getLogger(__name__)

SEND_TRIES = 6           # an alert to Discord or ntfy: tried this many times over a refusal (429) or a server error
RETRY_BASE_S = 1.0       # waits 1, 2, 4... s between (a 429's own wait when it gives one)
RETRY_MAX_S = 60.0
DEADLINE_S = 90.0        # a message given up on this long after it was raised (waiting its turn included)


def _wait(r: httpx.Response | None, i: int) -> float:
    """How long to wait before trying again after `r` (None: no answer): Discord's and ntfy's own say on a 429."""
    if r is not None and r.status_code == 429:
        try:
            got = float(r.json().get("retry_after"))
        except (ValueError, TypeError, AttributeError):
            got = None
        if got is None:
            try:
                got = float(r.headers.get("retry-after", ""))
            except ValueError:
                got = None
        if got is not None:
            return min(max(got, 0.0), RETRY_MAX_S)
    return min(RETRY_BASE_S * 2 ** i, RETRY_MAX_S)


class Notifier:
    def __init__(self, client: httpx.AsyncClient, discord_webhook: str | None, ntfy_url: str | None):
        self.client = client
        self.discord = discord_webhook   # Connections changes these while running
        self.ntfy = ntfy_url
        self.last: dict[str, tuple[bool, int]] = {}   # channel -> (delivered, when): the Connections status
        self.push = None   # webpush.Push: the app on phones and PCs that turned notifications on
        self.extra: list[tuple[str, str, str]] = []   # more Discord channels and ntfy topics: (id, kind, url)
        self.locks: dict[str, asyncio.Lock] = {}   # one message at a time to each channel (a burst at the open: in turn)
        self.failing: dict[str, float] = {}   # channel -> when its last message failed (monotonic): the ones raised
                                              # before then, queued behind it, try once

    async def _post(self, url: str, tries: int, queue: bool = True, **kw) -> httpx.Response:
        """POSTs to one channel, one message at a time (queue), again over a 429 or a server error (waiting what it
        asks), past Discord's rate limit before the next (X-RateLimit-Remaining 0: its Reset-After), until DEADLINE_S
        after it was raised. While the channel is failing, each queued message tries once, so none waits on the ones
        before it for minutes; one raised after the failure (a later alert) is tried in full. A 429 is waited out before
        the next queued one goes. Not again after no answer in time (it may have been taken: not posted twice)."""
        t0 = time.monotonic()
        if not queue:   # the Connections Test: now, not behind alerts waiting on a failing channel
            return await self._try(url, tries, t0, **kw)
        lock = self.locks.setdefault(url, asyncio.Lock())
        async with lock:
            behind = t0 <= self.failing.get(url, -1.0)
            try:
                r = await self._try(url, 1 if behind else tries, t0, **kw)
            except Exception:
                self.failing[url] = time.monotonic()
                raise
            if r.is_success:
                self.failing.pop(url, None)
            else:
                self.failing[url] = time.monotonic()
                if r.status_code == 429:   # its wait kept: the next one queued would only be refused too
                    await asyncio.sleep(_wait(r, 0))
            return r

    async def _try(self, url: str, tries: int, t0: float, **kw) -> httpx.Response:
        r = None
        for i in range(tries):
            try:
                r = await self.client.post(url, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout):   # never reached it: try again
                if i == tries - 1 or time.monotonic() - t0 + _wait(None, i) > DEADLINE_S:
                    raise
                await asyncio.sleep(_wait(None, i))
                continue
            if r.status_code != 429 and r.status_code < 500:
                if r.headers.get("x-ratelimit-remaining") == "0":
                    try:
                        await asyncio.sleep(min(float(r.headers.get("x-ratelimit-reset-after", "0")), RETRY_MAX_S))
                    except ValueError:
                        pass
                return r
            wait = _wait(r, i)
            if i == tries - 1 or time.monotonic() - t0 + wait > DEADLINE_S:
                break
            await asyncio.sleep(wait)
        return r

    @property
    def channels(self) -> list[str]:
        out = [n for n, url in (("discord", self.discord), ("ntfy", self.ntfy)) if url]
        out += [k for k in ("discord", "ntfy") if k not in out and any(kind == k for _, kind, _ in self.extra)]
        return out + ["app"] if self.push is not None and self.push.count() else out

    async def send(self, title: str, message: str) -> str:
        """Returns the channels that accepted the message, e.g. 'discord,ntfy'."""
        jobs = []
        if self.discord:
            jobs.append(("discord", self._discord(title, message)))
        if self.ntfy:
            jobs.append(("ntfy", self._ntfy(title, message)))
        for cid, kind, url in self.extra:
            if kind in ("discord", "ntfy"):
                jobs.append((cid, (self._discord if kind == "discord" else self._ntfy)(title, message, url)))
        if self.push is not None and self.push.count():
            jobs.append(("app", self._push(title, message)))
        results = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
        ok = []
        for (name, _), r in zip(jobs, results):
            self.last[name] = (r is True, int(time.time() * 1000))
            kind = name.rsplit("-", 1)[0]   # discord-2 is Discord
            if r is True:
                if kind not in ok:
                    ok.append(kind)
            else:
                log.warning("%s delivery failed: %s", name, type(r).__name__ if isinstance(r, Exception) else r)
        return ",".join(ok)

    async def _discord(self, title, message, url=None, tries: int = SEND_TRIES, queue: bool = True) -> bool:
        body = {"username": "Chart Alerts", "content": f"**{title}**\n{message}"}
        return (await self._post(url or self.discord, tries, queue, json=body)).is_success

    async def post_chart(self, url: str, caption: str, png: bytes | None, username: str = "Tatanka Charts",
                         channel: str = "discord_charts") -> bool:
        """A chart picture (or just the text) to a Discord webhook: Post to Discord, as the app's name (Branding).
        Mentions are never pinged."""
        payload = json.dumps({"username": username, "content": caption, "allowed_mentions": {"parse": []}})
        ok = False
        for _ in range(2):
            if png is None:
                r = await self.client.post(url, json=json.loads(payload), timeout=30)
            else:
                r = await self.client.post(url, data={"payload_json": payload}, files={"files[0]": ("chart.png", png, "image/png")}, timeout=30)
            if r.status_code == 429:
                await asyncio.sleep(min(float(r.json().get("retry_after", 1)), 5))
                continue
            ok = r.is_success
            break
        self.last[channel] = (ok, int(time.time() * 1000))
        return ok

    async def _push(self, title, message) -> bool:
        return await self.push.send(title, message) > 0

    async def _ntfy(self, title, message, url=None, tries: int = SEND_TRIES, queue: bool = True) -> bool:
        r = await self._post(url or self.ntfy, tries, queue, content=message.encode(),
                             headers={"Title": title, "Priority": "high", "Tags": "chart_with_upwards_trend"})
        return r.is_success
