import json, os, secrets, sqlite3, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, Response, session, send_file
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("STUDYSYNC_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("GROWPROS_PUBLIC") == "1",
)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studysync_data.sqlite3")
LEGACY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studysync_data.json")
DEFAULT = {"commits": [{"t": "Read loops theory", "d": 1}, {"t": "Solve 10 examples", "d": 0}, {"t": "Push code to group repo", "d": 0}],
           "sessions": [], "badges": [],
           "settings": {"leaderboard": True, "group_streak": True, "pomodoro_focus": 25, "pomodoro_break": 5},
           "pomodoro_completed": 0}
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "growpros-logo.png")

_AI_REQUESTS_BY_USER = {}
_AI_REQUESTS_TOTAL = []

def allow_deshika_request(user_id):
    now = time.monotonic()
    _AI_REQUESTS_TOTAL[:] = [stamp for stamp in _AI_REQUESTS_TOTAL if now - stamp < 3600]
    user_calls = [stamp for stamp in _AI_REQUESTS_BY_USER.get(user_id, []) if now - stamp < 3600]
    if len(user_calls) >= 20 or len(_AI_REQUESTS_TOTAL) >= 100:
        _AI_REQUESTS_BY_USER[user_id] = user_calls
        return False
    user_calls.append(now)
    _AI_REQUESTS_BY_USER[user_id] = user_calls
    _AI_REQUESTS_TOTAL.append(now)
    return True

def get_openai_api_key():
    return os.environ.get("OPENAI_API_KEY", "").strip()

def connect_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS user_state (user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS circles (id INTEGER PRIMARY KEY, invite_code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS circle_members (user_id INTEGER PRIMARY KEY REFERENCES users(id), circle_id INTEGER NOT NULL REFERENCES circles(id), joined_at TEXT NOT NULL)")
        missing = conn.execute("SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM circle_members)").fetchall()
        for user in missing:
            code = secrets.token_hex(4).upper()
            cur = conn.execute("INSERT INTO circles (invite_code, created_at) VALUES (?, ?)", (code, datetime.now().isoformat()))
            conn.execute("INSERT INTO circle_members (user_id, circle_id, joined_at) VALUES (?, ?, ?)", (user["id"], cur.lastrowid, datetime.now().isoformat()))

def circle_summary(user_id):
    today = datetime.now().date()
    with connect_db() as conn:
        circle = conn.execute("SELECT c.id, c.invite_code FROM circle_members m JOIN circles c ON c.id=m.circle_id WHERE m.user_id=?", (user_id,)).fetchone()
        if not circle:
            return {"invite_code": "", "member_count": 1, "shared_members": [], "streak": {"days": 0, "today_complete": False, "week": [False] * 7, "members": []}, "leaderboard": []}
        rows = conn.execute("SELECT u.id, u.username, s.data FROM circle_members m JOIN users u ON u.id=m.user_id LEFT JOIN user_state s ON s.user_id=u.id WHERE m.circle_id=? ORDER BY u.username", (circle["id"],)).fetchall()
    members = []
    for row in rows:
        data = json.loads(row["data"]) if row["data"] else json.loads(json.dumps(DEFAULT))
        settings = dict(DEFAULT["settings"])
        settings.update(data.get("settings") or {})
        daily = {}
        for entry in data.get("sessions", []):
            day = str(entry.get("date", ""))[:10]
            if day:
                daily[day] = daily.get(day, 0) + max(0, int(entry.get("seconds", 0)))
        today_seconds = daily.get(today.isoformat(), 0)
        commits = data.get("commits", [])
        goals = sum(bool(item.get("d")) for item in commits)
        last7 = sum(daily.get((today - timedelta(days=i)).isoformat(), 0) >= 3600 for i in range(7))
        members.append({"id": row["id"], "username": row["username"], "settings": settings, "daily": daily,
                        "seconds": today_seconds, "minutes": today_seconds // 60, "goals": goals,
                        "consistency": round(last7 / 7 * 100)})
    streak_members = [m for m in members if m["settings"].get("group_streak", True)]
    leaderboard = []
    for member in members:
        if not member["settings"].get("leaderboard", True):
            continue
        score = member["goals"] * 30 + member["consistency"] + min(member["minutes"], 120) / 4
        leaderboard.append({"username": member["username"], "you": member["id"] == user_id,
                            "minutes": member["minutes"], "goals": member["goals"],
                            "consistency": member["consistency"], "score": round(score)})
    leaderboard.sort(key=lambda item: (-item["score"], item["username"]))
    week_start = today - timedelta(days=today.weekday())
    week = [all(m["daily"].get((week_start + timedelta(days=i)).isoformat(), 0) >= 3600 for m in streak_members) for i in range(7)]
    today_complete = bool(streak_members) and week[today.weekday()]
    days = 0
    start_offset = 0 if today_complete else 1
    for offset in range(start_offset, 366):
        date_key = (today - timedelta(days=offset)).isoformat()
        if not streak_members or not all(m["daily"].get(date_key, 0) >= 3600 for m in streak_members):
            break
        days += 1
    streak = {"days": days, "today_complete": today_complete, "week": week,
              "members": [{"username": m["username"], "you": m["id"] == user_id, "minutes": m["minutes"]} for m in streak_members]}
    visible = [m for m in members if m["settings"].get("leaderboard", True) or m["settings"].get("group_streak", True)]
    shared = [{"username": m["username"], "you": m["id"] == user_id, "minutes": m["minutes"]} for m in visible]
    return {"invite_code": circle["invite_code"], "member_count": len(members), "shared_members": shared,
            "streak": streak, "leaderboard": leaderboard}

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify(error="Please log in first."), 401
        return fn(*args, **kwargs)
    return wrapped

def load():
    with connect_db() as conn:
        row = conn.execute("SELECT data FROM user_state WHERE user_id = ?", (session["user_id"],)).fetchone()
    if row:
        data = json.loads(row["data"])
        data.setdefault("settings", {}).update({
            key: value for key, value in DEFAULT["settings"].items()
            if key not in data.get("settings", {})
        })
        data.setdefault("pomodoro_completed", 0)
        return data
    return json.loads(json.dumps(DEFAULT))

def save(d):
    with connect_db() as conn:
        conn.execute("INSERT INTO user_state (user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
                     (session["user_id"], json.dumps(d)))

def default_for_first_user():
    if os.path.exists(LEGACY_DB):
        try:
            with open(LEGACY_DB, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict) and all(k in old for k in ("commits", "sessions", "badges")):
                return old
        except (OSError, ValueError):
            pass
    return json.loads(json.dumps(DEFAULT))

def today_seconds(d):
    t = datetime.now().date().isoformat()
    return sum(s["seconds"] for s in d["sessions"] if s["date"].startswith(t))

def badge(d, k):
    if k not in d["badges"]:
        d["badges"].append(k)

@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")

@app.get("/favicon.png")
def favicon():
    return send_file(LOGO_PATH, mimetype="image/png", conditional=True)

@app.get("/logo.png")
def logo():
    return send_file(LOGO_PATH, mimetype="image/png", conditional=True)

@app.get("/api/me")
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify(authenticated=False)
    with connect_db() as conn:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        session.clear()
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, username=row["username"])

@app.post("/api/signup")
def signup():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    if not 3 <= len(username) <= 24 or not all(c.isalnum() or c in "_-" for c in username):
        return jsonify(error="User ID must be 3–24 letters or numbers; _ and - are allowed."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400
    try:
        with connect_db() as conn:
            first_user = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
            cursor = conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                                  (username, generate_password_hash(password), datetime.now().isoformat()))
            user_id = cursor.lastrowid
            initial = default_for_first_user() if first_user else json.loads(json.dumps(DEFAULT))
            conn.execute("INSERT INTO user_state (user_id, data) VALUES (?, ?)", (user_id, json.dumps(initial)))
            code = secrets.token_hex(4).upper()
            circle = conn.execute("INSERT INTO circles (invite_code, created_at) VALUES (?, ?)", (code, datetime.now().isoformat()))
            conn.execute("INSERT INTO circle_members (user_id, circle_id, joined_at) VALUES (?, ?, ?)", (user_id, circle.lastrowid, datetime.now().isoformat()))
    except sqlite3.IntegrityError:
        return jsonify(error="That User ID is already taken."), 409
    session.clear()
    session["user_id"] = user_id
    return jsonify(ok=True, username=username)

@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    with connect_db() as conn:
        row = conn.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify(error="User ID or password is incorrect."), 401
    session.clear()
    session["user_id"] = row["id"]
    return jsonify(ok=True, username=row["username"])

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/state")
@login_required
def state():
    d = load(); d["today_seconds"] = today_seconds(d); d["circle"] = circle_summary(session["user_id"])
    return jsonify(d)

@app.get("/api/circle")
@login_required
def circle():
    return jsonify(circle_summary(session["user_id"]))

@app.post("/api/circle/join")
@login_required
def join_circle():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().upper()
    if not 6 <= len(code) <= 12 or not code.isalnum():
        return jsonify(error="Enter a valid circle code."), 400
    with connect_db() as conn:
        target = conn.execute("SELECT id, invite_code FROM circles WHERE invite_code=?", (code,)).fetchone()
        if not target:
            return jsonify(error="That circle code was not found."), 404
        current = conn.execute("SELECT circle_id FROM circle_members WHERE user_id=?", (session["user_id"],)).fetchone()
        if current and current["circle_id"] == target["id"]:
            return jsonify(ok=True, circle=circle_summary(session["user_id"]))
        conn.execute("INSERT INTO circle_members (user_id, circle_id, joined_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET circle_id=excluded.circle_id, joined_at=excluded.joined_at",
                     (session["user_id"], target["id"], datetime.now().isoformat()))
    return jsonify(ok=True, circle=circle_summary(session["user_id"]))

@app.post("/api/session")
@login_required
def add_session():
    d = load(); secs = int(request.json.get("seconds", 0))
    if secs > 5:
        d["sessions"].append({"date": datetime.now().isoformat(), "seconds": secs})
        badge(d, "first")
        if today_seconds(d) >= 3600: badge(d, "hour")
        save(d)
    group = circle_summary(session["user_id"])
    if len(group["shared_members"]) > 1 and all(member["minutes"] > 0 for member in group["shared_members"]):
        badge(d, "squad"); save(d); group = circle_summary(session["user_id"])
    return jsonify(today_seconds=today_seconds(d), badges=d["badges"], circle=group)

@app.post("/api/commits")
@login_required
def commits():
    d = load(); b = request.json
    if "add" in b: d["commits"].append({"t": b["add"], "d": 0})
    if "toggle" in b:
        i = int(b["toggle"]); d["commits"][i]["d"] = 1 - d["commits"][i]["d"]
    if d["commits"] and all(c["d"] for c in d["commits"]): badge(d, "goal")
    save(d)
    return jsonify(commits=d["commits"], badges=d["badges"])

@app.post("/api/settings")
@login_required
def settings():
    body = request.get_json(silent=True) or {}
    updates = body.get("settings") or {}
    if not isinstance(updates, dict):
        return jsonify(error="Settings must be an object."), 400
    allowed = {"leaderboard", "group_streak"}
    for key in ("leaderboard", "group_streak"):
        if key in updates and type(updates[key]) is not bool:
            return jsonify(error=f"{key} must be on or off."), 400
    for key, low, high in (("pomodoro_focus", 1, 90), ("pomodoro_break", 1, 30)):
        if key in updates:
            value = updates[key]
            if type(value) is not int or not low <= value <= high:
                return jsonify(error=f"{key} must be between {low} and {high} minutes."), 400
            allowed.add(key)
    if any(key not in allowed for key in updates):
        return jsonify(error="Unknown setting."), 400
    data = load()
    data["settings"].update(updates)
    save(data)
    return jsonify(settings=data["settings"])

@app.post("/api/badge")
@login_required
def add_badge():
    d = load(); badge(d, request.json["k"]); save(d)
    return jsonify(badges=d["badges"])

@app.post("/api/pomodoro-round")
@login_required
def complete_pomodoro_round():
    d = load()
    d["pomodoro_completed"] = int(d.get("pomodoro_completed", 0)) + 1
    if d["pomodoro_completed"] >= 4:
        badge(d, "pomo4")
    save(d)
    return jsonify(pomodoro_completed=d["pomodoro_completed"], badges=d["badges"])

@app.post("/api/deshika")
@login_required
def deshika():
    payload = request.get_json(silent=True) or {}
    q = payload.get("q", "")
    if not isinstance(q, str) or not q.strip():
        return jsonify(error="Enter a question first."), 400
    q = q.strip()
    if len(q) > 3000:
        return jsonify(error="Please keep each question under 3,000 characters."), 413

    api_key = get_openai_api_key()
    if api_key:
        if not allow_deshika_request(session.get("user_id")):
            return jsonify(reply="Deshika's request limit has been reached for now. Please try again later."), 429
        body = json.dumps({
            "model": os.environ.get("OPENAI_MODEL", "gpt-6-luna"),
            "instructions": "You are Deshika, a friendly AI study coach for college students. Be concise and encouraging. Explain concepts clearly, and give notes, text mind maps, or quizzes when asked.",
            "input": q,
            "max_output_tokens": 500,
            "reasoning": {"effort": "none"},
            "store": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=body,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                result = json.loads(response.read().decode("utf-8"))
            parts = []
            for item in result.get("output", []):
                if item.get("type") == "message":
                    parts.extend(
                        part.get("text", "") for part in item.get("content", [])
                        if part.get("type") == "output_text"
                    )
            answer = "\n".join(part for part in parts if part).strip()
            if not answer:
                return jsonify(reply="Deshika did not receive a text answer. Please try again."), 502
            return jsonify(reply=answer)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                message = "OpenAI rejected the saved API key. Check the host's OPENAI_API_KEY secret."
            elif error.code == 429:
                message = "OpenAI usage is currently limited. Check the API account's billing and usage limits."
            else:
                message = "Deshika could not get a response from OpenAI. Please try again later."
            return jsonify(reply=message), 502
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return jsonify(reply="Deshika could not connect to OpenAI. Please try again later."), 502

    l = q.lower()
    if "mind" in l:
        return jsonify(reply="Mind map: Loops -> for (known count), while (until condition), control (break, continue), pitfalls (infinite loop, off-by-one).")
    if "note" in l:
        return jsonify(reply="Notes: a loop repeats a block. Use for when you know the count, while when you don't. break exits, continue skips one round.")
    return jsonify(reply="Demo mode. Add OPENAI_API_KEY in the hosting settings to enable Deshika.")

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GrowPros</title>
<meta name="description" content="Grow together. One focused session at a time.">
<meta name="theme-color" content="#070b1c">
<link rel="icon" type="image/png" href="/favicon.png">
<style>
:root{--bg:#070b1c;--panel:#0e1530;--card:#141d3f;--line:#233060;--acc:#2fd0ff;--acc2:#3d6bff;--ok:#3ee8a5;--warn:#ffb44a;--tx:#e9f4ff;--mu:#8397b5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 "Trebuchet MS","Segoe UI",system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:20px 16px 96px}
h1{font:800 30px/1.1 "Trebuchet MS",sans-serif;margin:0}h2{font-size:16px;margin:0 0 10px}
.sub{color:var(--mu);margin:4px 0 18px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
.row{display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--card);border-radius:10px;margin:6px 0}
.row .g{flex:1}.mu{color:var(--mu);font-size:13px}
.dot{width:10px;height:10px;border-radius:50%;background:#56657f;flex:none}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px #3ee8a530}
button{font:inherit;font-weight:700;border:0;border-radius:10px;padding:9px 14px;background:var(--acc2);color:#fff;cursor:pointer}
button.ghost{background:var(--card);color:var(--tx);border:1px solid var(--line)}
button.stop{background:#5a1c33;color:#ff8aa3}
input{font:inherit;background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:10px;padding:9px 11px;width:100%}
.q input,.row input{width:auto}
.clock{font:800 54px/1 "Courier New",monospace;text-align:center;margin:8px 0 14px;letter-spacing:2px}
.btns{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
.bar{height:10px;background:var(--card);border-radius:9px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc2),var(--acc));transition:width .4s}
.streak{display:flex;gap:6px;margin:10px 0}
.streak b{flex:1;height:38px;border-radius:9px;background:var(--card);display:grid;place-items:center;font-size:12px;color:var(--mu)}
.streak b.hit{background:linear-gradient(160deg,#ff8a3d,#ffcf5a);color:#3a1d00}
.big{font:800 40px/1 "Trebuchet MS",sans-serif;color:var(--warn)}
nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--panel);border-top:1px solid var(--line)}
nav button{flex:1;background:none;color:var(--mu);border-radius:0;padding:12px 4px;font-size:12px}
nav button.on{color:var(--acc);box-shadow:inset 0 3px var(--acc)}
.pg{display:none}.pg.on{display:block}
.msg{padding:9px 12px;border-radius:12px;margin:6px 0;max-width:88%;white-space:pre-wrap}
.msg.ai{background:var(--card)}.msg.me{background:var(--acc2);margin-left:auto}
.q label{display:block;padding:9px 12px;background:var(--card);border-radius:10px;margin:6px 0;cursor:pointer}
.tag{font-size:12px;padding:2px 8px;border-radius:9px;background:var(--card);color:var(--acc)}
.sw{display:flex;justify-content:space-between;align-items:center;gap:8px}
#authGate{max-width:420px;margin:10vh auto;padding:20px}
#authGate .card{padding:24px}
#authGate label{display:block;margin:12px 0 5px;color:var(--mu)}
#authError{color:#ff8aa3;min-height:24px}
#appShell{padding-top:20px}
.brand-lockup{display:flex;align-items:center;gap:14px;margin-bottom:18px}
.brand-mark{width:62px;height:62px;flex:none}
.brand-mark.small{width:42px;height:42px}
.brand-copy h1{margin:0}
.brand-motto{color:var(--acc);font-weight:700;font-size:13px;margin:4px 0 0}
.brand-titleline{display:flex;align-items:center;gap:10px}
button.toggle-on{background:#113a35;color:#72efbd;border:1px solid #287b68;min-width:76px}
button.toggle-off{background:var(--card);color:var(--mu);border:1px solid var(--line);min-width:76px}
.big.streak-count{display:flex;align-items:center;gap:8px}
.sapling{font-size:30px}
.pomo-clock{font-size:48px}
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.settings-grid label{display:block;color:var(--mu);font-size:13px}
.settings-grid input{margin-top:5px}
</style></head><body>
<section id="authGate">
 <div class="card">
  <div class="brand-lockup"><img class="brand-mark" src="/logo.png" alt="GrowPros logo"><div class="brand-copy"><h1>GrowPros</h1><p class="brand-motto">Small steps. Steady focus. Shared growth.</p></div></div>
  <p class="sub">Sign in to keep your study progress private.</p>
  <label for="authUser">User ID</label><input id="authUser" autocomplete="username" maxlength="24" placeholder="Choose a user ID">
  <label for="authPass">Password</label><input id="authPass" type="password" autocomplete="current-password" placeholder="At least 8 characters">
  <div id="confirmWrap" style="display:none"><label for="authConfirm">Confirm password</label><input id="authConfirm" type="password" autocomplete="new-password" placeholder="Type the password again"></div>
  <p id="authError" role="alert"></p>
  <div class="btns"><button id="authSubmit">Log in</button><button class="ghost" id="authSwitch">Create account</button></div>
  <p class="mu" style="margin-top:14px">User IDs use 3–24 letters or numbers. Passwords need at least 8 characters.</p>
 </div>
</section>
<main id="appShell" style="display:none">
<div id="home" class="pg on">
 <div class="sw"><div class="brand-titleline"><img class="brand-mark small" src="/logo.png" alt=""><h1>GrowPros</h1></div><div><span class="tag" id="whoami"></span> <button class="ghost" id="logout">Log out</button></div></div><p class="sub">Small steps. Steady focus. Shared growth.</p>
 <div class="grid">
  <div class="card"><h2>Study room</h2><div class="clock" id="clk">00:00:00</div>
   <div class="btns"><button id="go">Start studying</button><button class="stop" id="stp">Stop</button></div>
   <p class="mu" style="text-align:center" id="roomNote">Completed focus time appears for circle members who opted in.</p></div>
  <div class="card"><h2>Circle progress</h2><p class="mu">Daily focused minutes from friends in your circle.</p><div id="pres"></div></div>
 </div>
 <div class="card"><div class="sw"><div><h2>Friend circle</h2><span class="tag" id="circleCount">1 member</span></div></div>
  <p class="mu">Share your code with friends, or enter theirs to join. Circle members can see your daily focus minutes and goals while the matching features are on.</p>
  <div class="row"><span class="g">Your invite code</span><b id="circleCode">—</b></div>
  <div style="display:flex;gap:8px;margin-top:10px"><input id="joinCode" maxlength="12" placeholder="Friend's circle code"><button id="joinCircle">Join circle</button></div><p id="circleMessage" class="mu" role="status"></p>
 </div>
 <div class="card" id="streakCard"><div class="sw"><div><h2>Friends streak</h2><span class="tag">🌱 each participating member needs 1 focused hour</span></div><button id="streakToggle" class="toggle-on" aria-pressed="true">On</button></div>
  <div id="streakOff" class="mu" style="display:none">Friend streak tracking is off for your account.</div>
  <div id="streakDetails"><div class="sw"><div><div class="big streak-count"><span class="sapling" aria-hidden="true">🌱</span><span id="sn">0</span></div><div class="mu">days in a row</div></div>
  <div class="mu" style="max-width:260px;text-align:right" id="sTxt"></div></div>
  <div class="streak" id="strk"></div></div></div>
</div>
<div id="mission" class="pg">
 <h1>Mission</h1><p class="sub">One shared goal, small daily commitments.</p>
 <div class="card"><div class="sw"><h2>Finish Python Module 1</h2><span class="tag">Due Friday</span></div>
  <div class="bar"><i id="mBar"></i></div><p class="mu" id="mTxt"></p></div>
 <div class="card"><h2>Today's commitments</h2><div id="commits"></div>
  <div style="display:flex;gap:8px;margin-top:10px"><input id="cIn" placeholder="Add a commitment"><button id="cAdd">Add</button></div></div>
 <div class="card"><h2>Deshika's suggested plan</h2><div id="plan" class="mu"></div>
  <button class="ghost" id="replan" style="margin-top:10px">Re-adjust plan</button></div>
</div>
<div id="board" class="pg">
 <h1>Team board</h1><p class="sub">Ranked by goals, consistency and teamwork, not just hours.</p>
 <div class="card"><div class="sw"><div><h2>Leaderboard</h2><p class="mu">Circle members appear here while their leaderboard is on.</p></div><button id="lbToggle" class="toggle-on" aria-pressed="true">On</button></div><div id="lb"></div></div>
 <div class="card"><h2>Badges</h2><div id="bd"></div></div>
</div>
<div id="ai" class="pg">
 <h1>Deshika AI</h1><p class="sub">Doubts, notes, mind maps, quizzes.</p>
 <div class="card"><div id="chat"><div class="msg ai">Hi, I'm Deshika. Try "notes on loops" or "mind map".</div></div>
  <div style="display:flex;gap:8px;margin-top:10px"><input id="aIn" placeholder="Ask Deshika anything"><button id="aGo">Send</button></div></div>
 <div class="card"><h2>End-of-session quiz</h2>
  <div style="display:flex;gap:8px"><input id="topic" placeholder="Topic you studied today"><button id="qGo">Quiz me</button></div>
  <div id="quiz" class="q"></div>
  <p class="mu">Tomorrow: a fresh set on the same topic. Weekly test: Sunday.</p></div>
</div>
<div id="focus" class="pg">
 <h1>Focus mode</h1><p class="sub">Work in short, focused rounds with proper breaks.</p>
 <div class="card"><div class="sw"><div><h2>Pomodoro timer</h2><span class="tag">Focus, then break</span></div><span class="tag" id="pRound">Round 1</span></div>
  <div class="clock pomo-clock" id="pClock">25:00</div><p class="mu" style="text-align:center" id="pStatus">Ready when you are. Start a focus round.</p>
  <div class="btns"><button id="pToggle">Start focus</button><button id="pReset" class="ghost">Reset</button></div>
  <div class="settings-grid"><label>Focus length (minutes)<input id="pmFocus" type="number" value="25" min="1" max="90"></label><label>Break length (minutes)<input id="pmBreak" type="number" value="5" min="1" max="30"></label></div>
 </div>
 <div class="card"><div class="sw"><div><h2>Block notifications</h2><p class="mu">Demo toggle only; it cannot silence notifications on the device.</p></div><button id="dnd" class="ghost">Off</button></div>
  <div class="row"><span class="g">Phone calls</span><span class="tag">always allowed</span></div>
  <div class="row"><span class="g">Other notifications</span><span class="tag" id="nState">allowed</span></div></div>
 <div class="card"><h2>Study circle ideas</h2><p class="mu">These are examples for a future discovery feature. For now, create a circle with an invite code on the Room tab.</p>
  <div class="row"><span class="g">Python practice</span><span class="tag">Example</span></div>
  <div class="row"><span class="g">Exam preparation</span><span class="tag">Example</span></div>
  <div class="row"><span class="g">Project team</span><span class="tag">Example</span></div></div>
</div>
</main>
<nav id="nav" style="display:none"><button data-p="home" class="on">Room</button><button data-p="mission">Mission</button><button data-p="board">Board</button><button data-p="ai">Deshika</button><button data-p="focus">Focus</button></nav>
<script>
const $=id=>document.getElementById(id);
const api=async(u,b)=>{const r=await fetch(u,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});const d=await r.json();if(!r.ok)throw new Error(d.error||"Something went wrong.");return d};
const S={people:[{n:"You",you:1,on:0,min:0,goals:0,cons:0}],circle:{},
 commits:[],days:[1,1,1,1,0,0,0],base:0,sec:0,t:null,badges:[],
 settings:{leaderboard:true,group_streak:true,pomodoro_focus:25,pomodoro_break:5},
 pomo:{phase:"focus",running:false,remaining:1500,endAt:0,timer:null,round:1},pomodoroCompleted:0,awardingSprout:false};
const fmt=s=>[s/3600|0,(s%3600)/60|0,s%60].map(x=>String(x).padStart(2,"0")).join(":");
const total=()=>S.base+S.sec;
const BADGES=[["first","First spark","Finish a study session"],["hour","One hour","Study 1 hour in a day"],["goal","Goal crusher","Complete all commitments"],["squad","Study squad","Everyone in your circle records focus time today"],["sprout","Circle keeper","Everyone opted into the shared streak reaches one focused hour"],["pomo4","Four rounds","Complete four Pomodoro focus rounds"]];
function renderPres(){const peers=S.people.filter(p=>!p.you);$("pres").innerHTML=peers.length?peers.map(p=>`<div class="row"><span class="dot"></span><span class="g"><b>${p.n}</b> <span class="mu">${p.min} min today</span></span></div>`).join(""):'<p class="mu">No friends in this circle yet. Share your invite code to study together.</p>'}
function setCircleData(circle,username){S.circle=circle||{};const rows=S.circle.shared_members||[];S.people=rows.map(m=>({n:m.username,you:!!m.you,on:0,min:m.minutes||0,goals:0,cons:0}));if(username&&!S.people.some(p=>p.you))S.people.unshift({n:username,you:1,on:0,min:Math.floor(total()/60),goals:0,cons:0});S.people.sort((a,b)=>Number(!!b.you)-Number(!!a.you));$("circleCode").textContent=S.circle.invite_code||"—";const n=S.circle.member_count||1;$("circleCount").textContent=n+" member"+(n===1?"":"s")}
async function refreshCircle(){try{const c=await api("/api/circle");setCircleData(c,$("whoami").textContent);renderPres();renderStreak();renderBoard()}catch(e){}}
function setToggle(button,enabled){button.textContent=enabled?"On":"Off";button.className=enabled?"toggle-on":"toggle-off";button.setAttribute("aria-pressed",String(enabled))}
async function updateSetting(key,value){const previous=S.settings[key];S.settings[key]=value;renderStreak();renderBoard();try{const d=await api("/api/settings",{settings:{[key]:value}});Object.assign(S.settings,d.settings);const c=await api("/api/circle");setCircleData(c,$("whoami").textContent);renderPres();renderStreak();renderBoard()}catch(e){S.settings[key]=previous;renderStreak();renderBoard()}}
function renderStreak(){const enabled=!!S.settings.group_streak;setToggle($("streakToggle"),enabled);$("streakDetails").style.display=enabled?"block":"none";$("streakOff").style.display=enabled?"none":"block";if(!enabled)return;
 const group=S.circle.streak||{},members=(group.members||[]).map(m=>({...m,minutes:m.you?Math.floor(total()/60):m.minutes}));const missing=members.filter(m=>m.minutes<60);const allHit=members.length>0&&missing.length===0;$("sn").textContent=(group.days||0)+(allHit&&!group.today_complete?1:0);
 $("strk").innerHTML=["M","T","W","T","F","S","S"].map((d,i)=>`<b class="${(group.week||[])[i]?"hit":""}">${d}</b>`).join("");
 $("sTxt").textContent=allHit?"Every participating friend reached 60 minutes today. The sapling streak is safe.":members.length?missing.map(p=>p.you?"You":p.username).join(", ")+" still need 60 focused minutes for today's streak.":"Join a circle with friends to grow a shared streak.";
 if(allHit&&!S.badges.includes("sprout")&&!S.awardingSprout){S.awardingSprout=true;api("/api/badge",{k:"sprout"}).then(d=>{S.badges=d.badges;renderBoard()}).catch(()=>{}).finally(()=>{S.awardingSprout=false})}}
function renderMission(){const n=S.commits.length,d=S.commits.filter(c=>c.d).length,pct=n?Math.round(d/n*100):0;
 const list=$("commits");list.replaceChildren();S.commits.forEach((c,i)=>{const label=document.createElement("label");label.className="row";const box=document.createElement("input");box.type="checkbox";box.dataset.c=i;box.checked=!!c.d;const title=document.createElement("span");title.className="g";title.textContent=c.t;label.append(box,title);list.append(label)});
 const team=Math.round((pct+62)/2);$("mBar").style.width=team+"%";
 $("mTxt").textContent=`Team progress ${team}%. You've done ${d} of ${n} commitments today.`;
 $("plan").innerHTML="4 days left, 3 topics remaining.<br>Today: loops (45 min). Wed: functions (60 min). Thu: practice set (60 min). Fri: revision + quiz (45 min).";
 S.people[0].goals=1+d}
function renderBoard(){const enabled=!!S.settings.leaderboard;setToggle($("lbToggle"),enabled);if(!enabled){$("lb").innerHTML='<p class="mu">Leaderboard is off for your account. Turn it on whenever you want.</p>';$("bd").innerHTML=BADGES.map(b=>`<div class="row"><span>${S.badges.includes(b[0])?"★":"🔒"}</span><span class="g"><b>${b[1]}</b> <span class="mu">${b[2]}</span></span></div>`).join("");return}const me=S.people[0];me.min=Math.floor(total()/60);me.cons=Math.min(100,80+(total()/180|0));
 const P=S.circle.leaderboard||[];
 $("lb").innerHTML=P.length?P.map((p,i)=>`<div class="row"><b style="width:26px">${i+1}</b><span class="g"><b>${p.username}${p.you?" (you)":""}</b><br><span class="mu">${p.goals} goals, ${p.consistency}% consistent, ${p.minutes} min</span></span><b style="color:var(--acc)">${p.score}</b></div>`).join(""):'<p class="mu">No one in this circle has opted into the leaderboard yet.</p>';
 $("bd").innerHTML=BADGES.map(b=>`<div class="row"><span>${S.badges.includes(b[0])?"★":"🔒"}</span><span class="g"><b>${b[1]}</b> <span class="mu">${b[2]}</span></span></div>`).join("")}
function all(){renderPres();renderStreak();renderMission();renderBoard()}
function paintPomodoro(){const p=S.pomo;const m=Math.floor(p.remaining/60),s=p.remaining%60;$('pClock').textContent=String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');$('pToggle').textContent=p.running?'Pause':p.phase==='focus'?'Start focus':'Start break';$('pRound').textContent=p.phase==='break'&&p.longBreak?'Long break':`Round ${p.round} of 4`;$('pmFocus').disabled=p.running;$('pmBreak').disabled=p.running}
function resetPomodoro(){const p=S.pomo;if(p.timer)clearInterval(p.timer);p.timer=null;p.running=false;p.phase='focus';p.longBreak=false;p.round=(S.pomodoroCompleted%4)+1;p.remaining=S.settings.pomodoro_focus*60;p.endAt=0;$('pStatus').textContent='Ready when you are. Start a focus round.';paintPomodoro()}
function pomodoroTick(){const p=S.pomo;p.remaining=Math.max(0,Math.ceil((p.endAt-Date.now())/1000));if(p.remaining){paintPomodoro();return}clearInterval(p.timer);p.timer=null;p.running=false;
 if(p.phase==='focus'){p.phase='break';S.pomodoroCompleted+=1;p.round=((S.pomodoroCompleted-1)%4)+1;p.longBreak=S.pomodoroCompleted%4===0;p.remaining=(p.longBreak?15:S.settings.pomodoro_break)*60;$('pStatus').textContent=p.longBreak?'Four focus rounds complete. Take a 15-minute long break.':'Focus round complete. Take a short break.';api('/api/pomodoro-round',{}).then(d=>{S.pomodoroCompleted=d.pomodoro_completed;S.badges=d.badges;renderBoard()}).catch(()=>{});api('/api/session',{seconds:S.settings.pomodoro_focus*60}).then(d=>{S.base=d.today_seconds;S.badges=d.badges;setCircleData(d.circle,$('whoami').textContent);$('clk').textContent=fmt(S.base);renderPres();renderStreak();renderBoard()}).catch(()=>{})}
 else{p.phase='focus';p.longBreak=false;p.round=(S.pomodoroCompleted%4)+1;p.remaining=S.settings.pomodoro_focus*60;$('pStatus').textContent='Break complete. Ready for your next focus round?'}paintPomodoro()}
function togglePomodoro(){const p=S.pomo;if(p.running){p.remaining=Math.max(0,Math.ceil((p.endAt-Date.now())/1000));clearInterval(p.timer);p.timer=null;p.running=false;$('pStatus').textContent='Paused. Pick up when you are ready.';paintPomodoro();return}p.running=true;p.endAt=Date.now()+p.remaining*1000;$('pStatus').textContent=p.phase==='focus'?'Focus round in progress.':'Break in progress.';p.timer=setInterval(pomodoroTick,250);paintPomodoro()}
$('pToggle').onclick=togglePomodoro;$('pReset').onclick=resetPomodoro;
$('pmFocus').addEventListener('change',async()=>{const value=Math.max(1,Math.min(90,parseInt($('pmFocus').value,10)||25));$('pmFocus').value=value;await updateSetting('pomodoro_focus',value);if(!S.pomo.running&&S.pomo.phase==='focus'){S.pomo.remaining=value*60;paintPomodoro()}});
$('pmBreak').addEventListener('change',async()=>{const value=Math.max(1,Math.min(30,parseInt($('pmBreak').value,10)||5));$('pmBreak').value=value;await updateSetting('pomodoro_break',value);if(!S.pomo.running&&S.pomo.phase==='break'){S.pomo.remaining=(S.pomo.longBreak?15:value)*60;paintPomodoro()}});
$('streakToggle').onclick=()=>updateSetting('group_streak',!S.settings.group_streak);
$('lbToggle').onclick=()=>updateSetting('leaderboard',!S.settings.leaderboard);
$("go").onclick=()=>{if(S.t)return;const t0=Date.now();S.sec=0;S.people[0].on=1;$("go").textContent="Studying…";
 S.t=setInterval(()=>{S.sec=Math.floor((Date.now()-t0)/1000);$("clk").textContent=fmt(total());renderStreak()},1000)};
$("stp").onclick=()=>{if(!S.t)return;clearInterval(S.t);S.t=null;const s=S.sec;S.people[0].on=0;$("go").textContent="Start studying";
 api("/api/session",{seconds:s}).then(d=>{S.base=d.today_seconds;S.sec=0;S.badges=d.badges;setCircleData(d.circle,$("whoami").textContent);$("clk").textContent=fmt(S.base);all()})};
$("joinCircle").onclick=async()=>{const code=$("joinCode").value.trim();if(!code){$("circleMessage").textContent="Enter your friend's circle code first.";return}try{const d=await api("/api/circle/join",{code});$("joinCode").value="";setCircleData(d.circle,$("whoami").textContent);$("circleMessage").textContent="You joined the circle. Your friend progress is now connected.";all()}catch(e){$("circleMessage").textContent=e.message}};
$("joinCode").addEventListener("keydown",e=>{if(e.key==="Enter")$("joinCircle").click()});
document.addEventListener("change",e=>{if(e.target.dataset.c!==undefined)api("/api/commits",{toggle:e.target.dataset.c}).then(d=>{S.commits=d.commits;S.badges=d.badges;api("/api/circle").then(c=>setCircleData(c,$("whoami").textContent));all()})});
$("cAdd").onclick=()=>{const v=$("cIn").value.trim();if(!v)return;$("cIn").value="";api("/api/commits",{add:v}).then(d=>{S.commits=d.commits;api("/api/circle").then(c=>{setCircleData(c,$("whoami").textContent);all()})})};
$("replan").onclick=()=>{$("plan").innerHTML="You're one topic behind. New plan: today loops (60 min), Wed functions + practice (75 min), Thu revision (45 min), Fri quiz + wrap-up (30 min)."};
$("nav").onclick=e=>{const p=e.target.dataset.p;if(!p)return;document.querySelectorAll(".pg,nav button").forEach(x=>x.classList.remove("on"));$(p).classList.add("on");e.target.classList.add("on");if(p=="board")renderBoard()};
function say(t,c){const m=document.createElement("div");m.className="msg "+c;m.textContent=t;$("chat").appendChild(m)}
$("aGo").onclick=()=>{const v=$("aIn").value.trim();if(!v)return;say(v,"me");$("aIn").value="";api("/api/deshika",{q:v}).then(d=>say(d.reply,"ai"))};
$("qGo").onclick=()=>{const t=$("topic").value.trim()||"Python loops";
 $("quiz").innerHTML=`<p><b>Quick check: ${t}</b></p><p>Which keyword exits a loop early?</p>`+["continue","break","pass"].map((o,i)=>`<label><input type="radio" name="q" value="${i}"> ${o}</label>`).join("")+'<button id="chk" style="margin-top:8px">Check</button><p id="res"></p>';
 $("chk").onclick=()=>{const v=document.querySelector('input[name=q]:checked');$("res").textContent=!v?"Pick an answer.":v.value==1?"Correct. break exits the loop.":"Not quite. continue skips one round; break exits."}};
$("dnd").onclick=()=>{const on=$("dnd").textContent=="Off";$("dnd").textContent=on?"On":"Off";$("dnd").className=on?"":"ghost";$("nState").textContent=on?"blocked for "+$("pmFocus").value+" min":"allowed"};
let createMode=false;
function authMode(){
 $("authSubmit").textContent=createMode?"Create account":"Log in";
 $("authSwitch").textContent=createMode?"Back to log in":"Create account";
 $("confirmWrap").style.display=createMode?"block":"none";
 $("authPass").autocomplete=createMode?"new-password":"current-password";
 $("authError").textContent="";
}
function showLogin(message=""){$("authGate").style.display="block";$("appShell").style.display="none";$("nav").style.display="none";$("authError").textContent=message}
async function openAccount(username){
 $("authGate").style.display="none";$("appShell").style.display="block";$("nav").style.display="flex";$("whoami").textContent=username;
 const d=await api("/api/state");S.commits=d.commits;S.badges=d.badges;S.base=d.today_seconds;S.settings={...S.settings,...(d.settings||{})};S.pomodoroCompleted=d.pomodoro_completed||0;setCircleData(d.circle,username);$("pmFocus").value=S.settings.pomodoro_focus;$("pmBreak").value=S.settings.pomodoro_break;$("clk").textContent=fmt(S.base);resetPomodoro();all();if(S.circleTimer)clearInterval(S.circleTimer);S.circleTimer=setInterval(refreshCircle,15000);
}
$("authSwitch").onclick=()=>{createMode=!createMode;authMode()};
$("authSubmit").onclick=async()=>{
 const username=$("authUser").value.trim(),password=$("authPass").value;
 $("authError").textContent="";
 if(createMode&&password!==$("authConfirm").value){$("authError").textContent="The passwords don't match.";return}
 try{const result=await api(createMode?"/api/signup":"/api/login",{username,password});$("authPass").value="";$("authConfirm").value="";await openAccount(result.username)}catch(e){$("authError").textContent=e.message}
};
$("authPass").addEventListener("keydown",e=>{if(e.key==="Enter")$("authSubmit").click()});
$("authUser").addEventListener("keydown",e=>{if(e.key==="Enter")$("authPass").focus()});
$("logout").onclick=async()=>{
 if(S.circleTimer){clearInterval(S.circleTimer);S.circleTimer=null}
 if(S.pomo.timer){clearInterval(S.pomo.timer);S.pomo.timer=null;S.pomo.running=false}
 if(S.t){clearInterval(S.t);S.t=null;const seconds=S.sec;S.sec=0;S.people[0].on=0;try{const d=await api("/api/session",{seconds});S.base=d.today_seconds}catch(e){}}
 try{await api("/api/logout")}finally{showLogin("You are logged out.");$("authPass").value=""}
};
api("/api/me").then(me=>me.authenticated?openAccount(me.username):showLogin()).catch(()=>showLogin("Could not connect to GrowPros. Refresh and try again."));
</script></body></html>"""

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=False, port=int(os.environ.get("PORT", os.environ.get("GROWPROS_PORT", "5001"))))
