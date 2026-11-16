"""Round 21 security: the restore command, login and devices after a restore, app notifications on sign-out, the
lock on wrong passwords from many addresses, and 500s from out-of-range query values."""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from chartsrv import auth, backup, setup
from chartsrv.store import Store
from test_app import client  # noqa: F401  the app on the sim feed

SERVER = Path(backup.__file__).resolve().parents[1]


def _env(tmp_path) -> dict:
    """The commands' own scratch database, backups, config and feeds folders, on the simulator."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHART_")}
    env.update(CHART_DB=str(tmp_path / "chart.db"), CHART_BACKUP_DIR=str(tmp_path / "bk"), CHART_FEED="sim",
               CHART_CONFIG_DIR=str(tmp_path / "config"), CHART_FEEDS_DIR=str(tmp_path / "feeds"),
               CHART_LEVELS_DIR=str(tmp_path / "logs"))
    return env


def test_restore_command_runs_with_an_alert_stored(tmp_path):
    """`python -m chartsrv.backup restore <file> --yes`, as docs/CONNECTIONS.md has it, on a database with an alert
    (which stores alert_id_high): the module's helpers are all defined before the command runs."""
    for d in ("config", "feeds", "logs"):
        (tmp_path / d).mkdir()
    st = Store(tmp_path / "chart.db")
    setup.init(st)
    st.add_alert("ES", "price", {"price": 5000.0, "cond": "cross"}, False, "a1")
    st.close()
    env = _env(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, "-m", "chartsrv.backup", *a], cwd=SERVER, env=env,   # noqa: E731
                                    capture_output=True, text=True, timeout=120)
    made = run("now")
    assert made.returncode == 0, made.stderr
    name = backup.files(tmp_path / "bk")[0].name
    st = Store(tmp_path / "chart.db")
    st.add_alert("ES", "price", {"price": 5100.0, "cond": "cross"}, False, "a2")   # made after the backup
    st.close()

    r = run("restore", name, "--yes")
    assert r.returncode == 0, r.stderr
    assert "restored:" in r.stdout and "NameError" not in r.stderr
    db = sqlite3.connect(tmp_path / "chart.db")
    assert db.execute("SELECT id, note FROM alerts").fetchall() == [(1, "a1")]
    assert db.execute("SELECT value FROM settings WHERE key='alert_id_high'").fetchone() == ("2",)   # a2's id stays used
    db.close()


def _sub(name: str) -> dict:
    """A device's push subscription (each its own address)."""
    from test_webpush import _device
    sub = _device()[2]
    return {**sub, "endpoint": f"https://web.push.apple.com/{name}"}


def test_restore_keeps_the_login_here(tmp_path, monkeypatch):
    """A phone is lost: it is signed out, its notifications removed and the password changed. Restoring an earlier
    backup (for its alerts) leaves all of that as it is: the password, the signed-in devices and the notification
    devices here are this install's, not the backup's. On an install with no password (a new PC) the backup's come."""
    from test_review_fixes import build_market
    m, _ = build_market(tmp_path, monkeypatch, CHART_BACKUP_DIR=str(tmp_path / "bk"))
    a, s = m.auth, m.store
    a.set_password("correct horse battery")
    pc, phone = a.start("pc"), a.start("lost phone")
    lost = m.push.subscribe(_sub("lost"), "lost phone")
    s.set_setting("watchlists_probe", {"a": 1})
    bk = backup.make(s.path, tmp_path / "bk")
    vapid = s.get_setting("push_vapid")

    a.save(auth.make_hash("a brand new secret"))   # Change password: every other device signed out
    a.end_all()
    pc2, tablet = a.start("pc"), a.start("tablet")
    m.push.remove(id=lost)
    m.push.subscribe(_sub("tablet"), "tablet")
    s.set_setting("watchlists_probe", {"a": 2})
    s.close()

    counts = backup.restore(s.path, bk)
    st = Store(s.path)
    a = auth.Auth(st)
    assert st.get_setting("watchlists_probe") == {"a": 1}   # what you made comes back
    assert a.check("a brand new secret") and not a.check("correct horse battery")
    assert a.session(phone) is None and a.session(pc) is None   # signed out since: still signed out
    assert a.session(pc2) and a.session(tablet)
    assert [r[0] for r in st.db.execute("SELECT label FROM push_subs")] == ["tablet"]
    assert st.get_setting("push_vapid") == vapid and counts["sessions"] == 2 and counts["push_subs"] == 1
    st.close()

    # a new PC: a fresh install with no password takes the backup's login with the rest
    new = tmp_path / "new" / "chart.db"
    new.parent.mkdir()
    st = Store(new)
    setup.init(st)
    st.close()
    backup.restore(new, bk)
    st = Store(new)
    a = auth.Auth(st)
    assert a.check("correct horse battery") and a.session(pc) and a.session(phone)
    assert [r[0] for r in st.db.execute("SELECT label FROM push_subs")] == ["lost phone"]
    assert st.get_setting("push_vapid") == vapid
    st.close()


def _labels(m) -> list[str]:
    """The app-notification devices kept (read on a connection of the test's own: the app's is its thread's)."""
    db = sqlite3.connect(m.store.path)
    try:
        return sorted(r[0] for r in db.execute("SELECT label FROM push_subs"))
    finally:
        db.close()


def test_signing_a_device_out_ends_its_app_notifications(client, monkeypatch):  # noqa: F811
    """A device's app notifications go with its sign-in: signed out (from another device, by itself, Sign out
    everywhere else, Change password, or the PC's `auth set`), it gets no more alerts. The device changing the
    password keeps its own. One turned on while no password was set belongs to no sign-in: it stays."""
    from test_hunt6_secfoot import LOCAL, PW
    from test_redesign_tsnet_host import PC, SERVE
    m = client.app.state.market
    devices = lambda: _labels(m)   # noqa: E731

    def sub(name, h):
        r = client.post("/api/push/subscribe", json={"subscription": _sub(name), "label": name}, headers=h)
        assert r.status_code == 200, r.text

    def took(r):   # the cookie the answer set, kept apart from the client's own
        client.cookies.clear()
        return {"cookie": r.headers["set-cookie"].split(";")[0]}
    sub("before login", LOCAL)   # login off: no sign-in to tie it to
    r = client.put("/api/auth/password", json={"new": PW, "label": "PC"}, headers=LOCAL)
    pc = {**LOCAL, **took(r)}

    def sign_in(label, ip):
        h = {**SERVE, "origin": f"https://{PC}", "x-forwarded-for": ip}
        r = client.post("/api/auth/login", json={"password": PW, "label": label}, headers=h)
        assert r.status_code == 200
        return {**h, **took(r)}
    phone, tablet, laptop = sign_in("phone", "100.64.0.7"), sign_in("tablet", "100.64.0.8"), sign_in("laptop", "100.64.0.9")
    for name, h in (("phone", phone), ("tablet", tablet), ("laptop", laptop), ("PC", pc)):
        sub(name, h)
    assert devices() == ["PC", "before login", "laptop", "phone", "tablet"]

    sid = {s["label"]: s["id"] for s in client.get("/api/auth/sessions", headers=pc).json()["sessions"]}
    assert client.delete(f"/api/auth/sessions/{sid['phone']}", headers=pc).status_code == 204   # the lost phone
    assert devices() == ["PC", "before login", "laptop", "tablet"]
    assert client.post("/api/auth/logout", headers=tablet).status_code == 200   # signs itself out
    assert devices() == ["PC", "before login", "laptop"]
    assert client.post("/api/auth/sessions/end-others", headers=pc).json() == {"ended": 1}
    assert devices() == ["PC", "before login"]

    laptop = sign_in("laptop", "100.64.0.9")
    sub("laptop", laptop)
    r = client.put("/api/auth/password", json={"current": PW, "new": PW + "2", "label": "PC"}, headers=pc)
    assert r.status_code == 200
    pc = {**LOCAL, **took(r)}
    assert devices() == ["PC", "before login"]   # the PC's own stay, with its new sign-in
    assert client.post("/api/auth/logout", headers=pc).status_code == 200
    assert devices() == ["before login"]

    assert client.post("/api/auth/login", json={"password": PW + "2", "label": "PC"}, headers=LOCAL).status_code == 200
    client.cookies.clear()
    laptop = {**SERVE, "origin": f"https://{PC}", "x-forwarded-for": "100.64.0.9"}
    r = client.post("/api/auth/login", json={"password": PW + "2", "label": "laptop"}, headers=laptop)
    laptop = {**laptop, **took(r)}
    sub("laptop", laptop)
    answers = iter(["another one!", "another one!"])
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt="": next(answers))
    assert auth.main(["set"]) == 0   # on the PC: every device signed out
    assert devices() == ["before login"]


def test_login_off_leaves_app_notifications(client):  # noqa: F811
    """Login off lets every device in: their notifications stay as they are."""
    from test_hunt6_secfoot import LOCAL, PW
    r = client.put("/api/auth/password", json={"new": PW, "label": "PC"}, headers=LOCAL)
    pc = {**LOCAL, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()
    assert client.post("/api/push/subscribe", json={"subscription": _sub("pc"), "label": "PC"}, headers=pc).status_code == 200
    assert client.put("/api/auth/password", json={"current": PW, "new": None}, headers=pc).json() == {"enabled": False}
    assert _labels(client.app.state.market) == ["PC"]


def test_devices_from_before_belong_to_no_sign_in(tmp_path):
    """A database from before: its app-notification devices get the sign-in column, empty, and a sign-out of every
    device leaves them (they are removed by hand)."""
    from chartsrv import webpush
    st = Store(tmp_path / "chart.db")
    with st.db:
        st.db.execute("CREATE TABLE push_subs (id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, "
                      "auth TEXT NOT NULL, label TEXT NOT NULL, created INTEGER NOT NULL, last_ok INTEGER, "
                      "last_error TEXT NOT NULL DEFAULT '')")
        st.db.execute("INSERT INTO push_subs (endpoint, p256dh, auth, label, created) VALUES ('https://web.push.apple.com/a', "
                      "'k', 'a', 'old phone', 1)")
    p = webpush.Push(st, None)
    assert [tuple(r) for r in st.db.execute("SELECT label, session FROM push_subs")] == [("old phone", None)]
    p.subscribe(_sub("new"), "new phone", "abcdef012345")
    assert p.signed_out() == 1 and [d["label"] for d in p.devices()] == ["old phone"]
    st.close()


def _db(m, sql: str, *args):
    """A write on a connection of the test's own (the app's is its thread's)."""
    db = sqlite3.connect(m.store.path)
    try:
        with db:
            db.execute(sql, args)
    finally:
        db.close()


def test_opening_the_app_ties_a_devices_notifications_to_its_sign_in(client):  # noqa: F811
    """App notifications turned on before they had a sign-in (an older version: every device there at the update), or
    with a sign-in that has since run out, were left by signing the device out: a lost phone kept getting every alert.
    The page asks POST /api/push/mine once it is connected (app.js pushHello), and the server ties them to the sign-in
    it opens the app with, so the sign-out ends them. It never adds back one it no longer has."""
    from test_hunt6_secfoot import LOCAL, PW
    from test_redesign_tsnet_host import PC, SERVE
    m = client.app.state.market
    r = client.put("/api/auth/password", json={"new": PW, "label": "PC"}, headers=LOCAL)
    pc = {**LOCAL, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()
    via = {**SERVE, "origin": f"https://{PC}", "x-forwarded-for": "100.64.0.7"}

    def sign_in(label):
        r = client.post("/api/auth/login", json={"password": PW, "label": label}, headers=via)
        assert r.status_code == 200
        client.cookies.clear()
        return {**via, "cookie": r.headers["set-cookie"].split(";")[0]}

    def sign_out(label):
        sid = {s["label"]: s["id"] for s in client.get("/api/auth/sessions", headers=pc).json()["sessions"]}
        assert client.delete(f"/api/auth/sessions/{sid[label]}", headers=pc).status_code == 204

    def opens(h, name):   # the page, connected: its subscription's id, or None
        r = client.post("/api/push/mine", json={"endpoint": _sub(name)["endpoint"]}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    # turned on before the update: the column added then is empty
    phone = sign_in("phone")
    did = client.post("/api/push/subscribe", json={"subscription": _sub("phone"), "label": "phone"}, headers=phone).json()["id"]
    _db(m, "UPDATE push_subs SET session=NULL")
    assert opens(phone, "phone") == did
    sign_out("phone")
    assert _labels(m) == [] and client.get("/api/health", headers=pc).json()["notify"] == []

    # kept only for alerts: its sign-in ran out (its notifications stay), then it signs in again
    tablet = sign_in("tablet")
    assert client.post("/api/push/subscribe", json={"subscription": _sub("tablet"), "label": "tablet"},
                       headers=tablet).status_code == 200
    _db(m, "UPDATE sessions SET seen=seen-? WHERE label='tablet'", (auth.SESSION_DAYS + 1) * 86_400_000)
    assert client.get("/api/auth", headers=tablet).json()["signed_in"] is False and _labels(m) == ["tablet"]
    tablet = sign_in("tablet")
    assert opens(tablet, "tablet")
    sign_out("tablet")
    assert _labels(m) == []

    # signed out with its notifications: signed in again, they stay off until it turns them on
    phone = sign_in("phone")
    assert opens(phone, "phone") is None and _labels(m) == []
    assert client.post("/api/push/mine", json={"endpoint": 5}, headers=phone).status_code == 400
    assert client.post("/api/push/mine", json={"endpoint": "https://web.push.apple.com/phone"}).status_code == 401


def test_a_password_set_while_login_was_off_keeps_this_devices_notifications(client):  # noqa: F811
    """The PC turned app notifications on while signed in, the login was turned off, and later the PC sets a password
    again: there is no sign-in of its own to keep, so every device's went, the PC's too. Its page now sends its endpoint
    with the password (app.js loginPart, the setup guide): its own stay, with its new sign-in. Every other device's
    from an earlier sign-in go, as on any new password."""
    from test_hunt6_secfoot import LOCAL, PW
    from test_redesign_tsnet_host import PC, SERVE
    m = client.app.state.market
    r = client.put("/api/auth/password", json={"new": PW, "label": "PC"}, headers=LOCAL)
    pc = {**LOCAL, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()
    assert client.post("/api/push/subscribe", json={"subscription": _sub("pc"), "label": "PC"}, headers=pc).status_code == 200
    via = {**SERVE, "origin": f"https://{PC}", "x-forwarded-for": "100.64.0.7"}
    r = client.post("/api/auth/login", json={"password": PW, "label": "phone"}, headers=via)
    phone = {**via, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()
    assert client.post("/api/push/subscribe", json={"subscription": _sub("phone"), "label": "phone"},
                       headers=phone).status_code == 200
    assert client.put("/api/auth/password", json={"current": PW, "new": None}, headers=pc).json() == {"enabled": False}
    client.cookies.clear()
    assert _labels(m) == ["PC", "phone"]   # login off leaves them

    r = client.put("/api/auth/password", json={"new": PW + "2", "label": "PC", "push": _sub("pc")["endpoint"]}, headers=LOCAL)
    assert r.status_code == 200
    pc = {**LOCAL, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()
    assert _labels(m) == ["PC"]
    assert client.post("/api/auth/logout", headers=pc).status_code == 200   # its own go with its new sign-in
    assert _labels(m) == []


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_page_asks_once_and_sends_its_endpoint_with_a_password():
    """app.js: once connected, a page with a push subscription asks POST /api/push/mine once (not a chart of a layout,
    whose page around it asks) and keeps the id the server has, or none; the Login card sends the endpoint with a new
    password, and leaves it out when the browser has none."""
    from test_hunt_web_ui import app_js, run_app_js
    out = run_app_js(app_js("const pushIdGet", "function pushWhyNot(", "async function pushSub(", "const pushEndpoint",
                            "function loginPart("), """
const APP = /*APP*/;
function page({ cell = null, sub = { endpoint: 'https://web.push.apple.com/me' }, answer = { id: 3 } } = {}) {
  const ls = new Map([['pushId', '7']]), calls = [];
  const c = { console, TK_CELL: cell, isSecureContext: true, PushManager: class {}, Notification: class {},
    localStorage: { getItem: (k) => ls.get(k) ?? null, setItem: (k, v) => ls.set(k, v), removeItem: (k) => ls.delete(k) },
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0) Chrome/130.0', maxTouchPoints: 0,
      serviceWorker: { getRegistration: async () => ({ pushManager: { getSubscription: async () => sub } }) } },
    matchMedia: () => ({ matches: false }),
    api: async (method, path, body) => { calls.push([method, path, body]); return answer; },
    cnEl: (tag, props = {}, ...kids) => ({ tag, ...props, kids, append(...more) { kids.push(...more); } }),
    onMenuPress() {}, ax: { date: { format: () => '' } }, $: () => null };
  c.window = c;
  vm.createContext(c);
  vm.runInContext(APP + ';this.pushHello = pushHello; this.pushIdGet = pushIdGet; this.loginPart = loginPart;', c);
  return { c, calls };
}
const find = (e, f) => e && (f(e) ? e : (e.kids || []).map((k) => find(k, f)).find(Boolean));
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };
const res = {};
let p = page();
await p.c.pushHello(); await p.c.pushHello();
res.once = { calls: p.calls, id: p.c.pushIdGet() };
p = page({ answer: { id: null } });
await p.c.pushHello();
res.gone = p.c.pushIdGet();
p = page({ cell: 1 }); await p.c.pushHello();
const cell = p.calls.length;
p = page({ sub: null }); await p.c.pushHello();
res.none = [cell, p.calls.length];
for (const sub of [{ endpoint: 'https://web.push.apple.com/me' }, null]) {
  p = page({ sub });
  const card = p.c.loginPart({ login: { enabled: false } });
  for (const ph of ['Password (8+ characters)', 'New password (8+ characters)', 'The same again']) {
    const i = find(card, (e) => e.placeholder === ph);
    if (i) i.value = 'correct horse';
  }
  find(card, (e) => e.textContent === 'Turn on login').onclick();
  await settle();
  (res.login ||= []).push(p.calls[0]);
}
out(res);""")
    assert out["once"] == {"calls": [["POST", "/api/push/mine", {"endpoint": "https://web.push.apple.com/me"}]], "id": 3}
    assert out["gone"] == 0 and out["none"] == [0, 0]
    assert out["login"] == [
        ["PUT", "/api/auth/password", {"new": "correct horse", "label": "Windows PC · Chrome", "push": "https://web.push.apple.com/me"}],
        ["PUT", "/api/auth/password", {"new": "correct horse", "label": "Windows PC · Chrome"}]]


def test_restore_onto_an_install_whose_login_was_turned_off(tmp_path, monkeypatch):
    """Forgot the password (PHONE.md): `auth off`, then an earlier backup restored for its alerts before a new password
    is set. The login here was turned off, not never set: it stays off, and the backup's password, the lost phone's
    sign-in and its app notifications don't come back."""
    from test_review_fixes import build_market
    m, _ = build_market(tmp_path, monkeypatch, CHART_BACKUP_DIR=str(tmp_path / "bk"))
    a, s = m.auth, m.store
    a.set_password("correct horse battery")
    a.start("pc")
    phone = a.start("lost phone")
    m.push.subscribe(_sub("lost"), "lost phone", auth.session_id(phone))
    s.set_setting("watchlists_probe", {"a": 1})
    bk = backup.make(s.path, tmp_path / "bk")
    assert a.end(phone) and m.push.signed_out(auth.session_id(phone)) == 1   # signed out from the PC
    a.turn_off()   # python -m chartsrv.auth off
    s.close()

    said = []
    counts = backup.restore(s.path, bk, say=said.append)
    st = Store(s.path)
    a = auth.Auth(st)
    assert st.get_setting("watchlists_probe") == {"a": 1}
    assert not a.enabled and st.get_setting(auth.SETTING) == {} and not a.check("correct horse battery")
    assert a.session(phone) is None and a.sessions() == []
    assert st.db.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0] == 0
    assert counts["sessions"] == 0 and counts["push_subs"] == 0
    assert any(x.startswith("The login stays as it was here, off") for x in said), said
    st.close()


def test_guesses_from_many_addresses_lock_out_neither_the_pc_nor_a_signed_in_device(client):  # noqa: F811
    """Someone without the password sends wrong ones from many addresses: every other device's sign-in waits (they
    can't go on guessing), but the PC itself still signs in, and a signed-in device can still change the password.
    Each keeps the limit of its own address."""
    from test_hunt6_secfoot import LOCAL, PW
    from test_redesign_tsnet_host import PC, SERVE
    m = client.app.state.market
    assert client.put("/api/auth/password", json={"new": PW, "label": "PC"}, headers=LOCAL).status_code == 200
    client.cookies.clear()
    phone = {**SERVE, "origin": f"https://{PC}", "x-forwarded-for": "100.64.0.7"}
    r = client.post("/api/auth/login", json={"password": PW, "label": "phone"}, headers=phone)
    phone = {**phone, "cookie": r.headers["set-cookie"].split(";")[0]}
    client.cookies.clear()

    guess = lambda i, ip: client.post("/api/auth/login", json={"password": f"guess{i}xx"},   # noqa: E731
                                      headers={"x-forwarded-for": ip}).status_code
    assert [guess(i, f"100.64.1.{i % 4}") for i in range(auth.FAIL_ALL)] == [401] * auth.FAIL_ALL
    assert client.post("/api/auth/login", json={"password": PW}, headers={"x-forwarded-for": "100.64.2.1"}).status_code == 429
    assert guess(99, "100.64.3.1") == 429 and sum(map(len, m.auth.fails.values())) == auth.FAIL_ALL   # still capped

    assert client.post("/api/auth/login", json={"password": PW, "label": "PC"}, headers=LOCAL).status_code == 200
    client.cookies.clear()
    assert client.put("/api/auth/password", json={"current": "not it at all", "new": "x" * 10}, headers=phone).status_code == 403
    r = client.put("/api/auth/password", json={"current": PW, "new": PW + "2", "label": "phone"}, headers=phone)
    assert r.status_code == 200
    client.cookies.clear()

    # the PC's own address still has its limit
    codes = [client.post("/api/auth/login", json={"password": f"pc guess {i}"}, headers=LOCAL).status_code
             for i in range(auth.FAIL_MAX + 1)]
    assert codes == [401] * auth.FAIL_MAX + [429]
    assert client.post("/api/auth/login", json={"password": PW + "2"}, headers=LOCAL).status_code == 429


def test_fills_and_journal_refuse_times_out_of_range(client):  # noqa: F811
    """start and end past SQLite's whole numbers (or before 1970, or after 2100): a 422 like /api/older's, not a 500."""
    for path in ("/api/fills", "/api/journal", "/api/journal?flat=true&"):
        path = path if path.endswith("&") else path + "?"
        for q in ("start=99999999999999999999999&end=1", "start=0&end=9223372036854775808",
                  "start=-99999999999999999999999&end=1", "start=-1&end=1", "start=0&end=4102444800001"):
            assert client.get(f"{path}{q}").status_code == 422, (path, q)
        assert client.get(f"{path}start=0&end=4102444800000").status_code == 200


