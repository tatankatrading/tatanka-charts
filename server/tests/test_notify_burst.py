"""A burst of alerts (the RTH open: gaps, AMT and level alerts within a second) against Discord's webhook rate limit:
each is sent, in turn, waiting what Discord asks (before: two tries, at most 5 s apart, all at once: some lost)."""
import asyncio
import json
import time

import httpx

from chartsrv import notify
from chartsrv.notify import Notifier

WEBHOOK = "https://discord.invalid/api/webhooks/1/x"   # never contacted: a mock transport answers


def test_every_alert_of_a_burst_reaches_discord_in_order(monkeypatch):
    monkeypatch.setattr(notify, "RETRY_MAX_S", 1.0)
    hits, got = [], []

    def discord(req: httpx.Request):   # 3 a second, as a webhook's bucket
        now = time.monotonic()
        hits[:] = [t for t in hits if now - t < 1.0]
        if len(hits) >= 3:
            return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": round(1.0 - (now - hits[0]), 3)})
        hits.append(now)
        got.append(json.loads(req.content)["content"])
        return httpx.Response(204)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(discord)) as c:
            n = Notifier(c, WEBHOOK, None)
            return await asyncio.gather(*(n.send("ES alert", f"alert {i}") for i in range(8)))
    assert asyncio.run(go()) == ["discord"] * 8
    assert got == [f"**ES alert**\nalert {i}" for i in range(8)]


def test_a_server_error_is_tried_again(monkeypatch):
    monkeypatch.setattr(notify, "RETRY_BASE_S", 0.01)
    calls = []

    def ntfy(req):
        calls.append(1)
        return httpx.Response(502 if len(calls) < 3 else 200)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ntfy)) as c:
            return await Notifier(c, None, "https://ntfy.invalid/topic").send("NQ alert", "NQ above 25000")
    assert asyncio.run(go()) == "ntfy" and len(calls) == 3
