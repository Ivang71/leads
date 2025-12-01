import time, logging, hashlib, json
from aiohttp import web
from ..core.processor import process_query
from ..stats import record_request_stat
from .. import config
from ..storage.users import mark_subscription_paid
from ..storage.db import get_pool

async def health(_: web.Request) -> web.Response:
	return web.Response(text="ok")

async def test(request: web.Request) -> web.Response:
	t0 = time.monotonic()
	ok = False
	q = (request.query.get("q") or "").strip()
	if not q:
		resp = web.json_response({"error": "q missing"}, status=400)
		await record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": 0})
		return resp
	try:
		res = await process_query(q)
	except Exception as e:
		logging.exception("test failed")
		return web.json_response({"error": "processing failed", "reason": str(e)}, status=500)
	final_text = (res.get("final_text") or "").strip()
	ms_len = int(res.get("ms_len", 0))
	ok = bool(res.get("ok", False))
	if not final_text:
		await record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": False, "q_len": len(q), "ms_len": ms_len, "out_len": 0})
		return web.json_response({"error": "no answer produced"}, status=502)
	resp = web.Response(text=final_text)
	await record_request_stat({
		"ts": int(time.time()),
		"source": "http_test",
		"dur": round(max(0.0, time.monotonic() - t0), 1),
		"ok": ok,
		"q_len": len(q),
		"ms_len": ms_len,
		"out_len": len(final_text),
	})
	return resp

async def freekassa_notify(request: web.Request) -> web.Response:
	data = await (request.post() if request.method == "POST" else request.query)
	m_id = (data.get("MERCHANT_ID") or "").strip()
	amount = (data.get("AMOUNT") or "").strip()
	order_id = (data.get("MERCHANT_ORDER_ID") or "").strip()
	sign = (data.get("SIGN") or "").strip()
	uid = (data.get("us_uid") or "").strip()
	if not (config.FREEKASSA_SECRET2 and m_id and amount and order_id and sign and uid):
		return web.Response(text="bad", status=400)
	raw = f"{m_id}:{amount}:{order_id}:{config.FREEKASSA_SECRET2}"
	expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
	if expected.lower() != sign.lower():
		logging.warning("freekassa bad sign")
		return web.Response(text="bad sign", status=400)
	try:
		await mark_subscription_paid(uid, 30)
	except Exception:
		logging.exception("freekassa notify failed to mark subscription")
	return web.Response(text="OK")

async def freekassa_success(_: web.Request) -> web.Response:
	return web.Response(text="Payment successful. You can close this tab and return to Telegram.", content_type="text/html; charset=utf-8")

async def freekassa_fail(_: web.Request) -> web.Response:
	return web.Response(text="Payment failed or was cancelled. You can close this tab and try again from Telegram.", content_type="text/html; charset=utf-8")


async def admin_page(_: web.Request) -> web.Response:
	html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Leads Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background: #020617; color: #e5e7eb; }
    .card { max-width: 960px; margin: 0 auto; background: #020617; border: 1px solid #1f2937; border-radius: 16px; padding: 16px; }
    input, button, select { font: inherit; }
    input[type=password] { padding: 6px 8px; border-radius: 6px; border: 1px solid #4b5563; background: #020617; color: #e5e7eb; }
    button { padding: 6px 10px; border-radius: 6px; border: 1px solid #4b5563; background: #111827; color: #e5e7eb; cursor: pointer; }
    button:hover { background: #1f2937; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    th, td { border: 1px solid #1f2937; padding: 4px 6px; text-align: left; }
    th { background: #030712; }
    .row { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
    .muted { color: #9ca3af; font-size: 12px; }
    .pill { display: inline-block; padding: 2px 6px; border-radius: 999px; border: 1px solid #4b5563; font-size: 11px; }
    .hidden { display: none; }
    pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; background: #020617; border: 1px solid #1f2937; padding: 8px; border-radius: 6px; max-height: 260px; overflow: auto; }
  </style>
</head>
<body>
  <div class="card">
    <h1 style="margin-top:0;margin-bottom:8px;">Leads Admin</h1>
    <div id="login-block">
      <div class="row">
        <label>Password: <input type="password" id="admin-pass"></label>
        <button id="login-btn">Login</button>
        <span class="muted">Flag is stored in this browser only.</span>
      </div>
      <div id="login-error" class="muted" style="color:#f97373;"></div>
    </div>
    <div id="app" class="hidden">
      <div class="row">
        <button id="tab-users">Users</button>
        <button id="tab-messages">Messages</button>
        <span class="pill" id="status-pill"></span>
        <span class="muted" id="summary"></span>
      </div>
      <div id="users-view">
        <div class="row">
          <label>User id: <input type="number" id="filter-user-id" style="width:110px;"></label>
          <label>Telegram id: <input type="number" id="filter-telegram-id" style="width:140px;"></label>
          <button id="load-users">Load</button>
        </div>
        <div id="users-table-wrap"></div>
      </div>
      <div id="messages-view" class="hidden">
        <div class="row">
          <label>User id: <input type="number" id="msg-user-id" style="width:110px;"></label>
          <button id="load-messages">Load messages</button>
        </div>
        <div id="messages-wrap"></div>
      </div>
    </div>
  </div>
  <script>
    (function(){
      var PASS_KEY = "leads_admin_ok";
      var PASS_VAL_KEY = "leads_admin_pass";
      var passInput = document.getElementById("admin-pass");
      var loginBtn = document.getElementById("login-btn");
      var loginBlock = document.getElementById("login-block");
      var app = document.getElementById("app");
      var loginError = document.getElementById("login-error");
      var tabUsers = document.getElementById("tab-users");
      var tabMessages = document.getElementById("tab-messages");
      var usersView = document.getElementById("users-view");
      var messagesView = document.getElementById("messages-view");
      var statusPill = document.getElementById("status-pill");
      var summary = document.getElementById("summary");
      var adminPass = "";

      function setStatus(text) { statusPill.textContent = text || ""; }
      function setSummary(text) { summary.textContent = text || ""; }

      function loadStoredPass() {
        try {
          if (localStorage.getItem(PASS_KEY) === "1") {
            adminPass = localStorage.getItem(PASS_VAL_KEY) || "";
          }
        } catch (e) {}
      }

      function storePass(v) {
        try {
          localStorage.setItem(PASS_KEY, "1");
          localStorage.setItem(PASS_VAL_KEY, v);
        } catch (e) {}
      }

      function showApp() {
        loginBlock.classList.add("hidden");
        app.classList.remove("hidden");
      }

      function activateTab(tab) {
        if (tab === "users") {
          usersView.classList.remove("hidden");
          messagesView.classList.add("hidden");
        } else {
          usersView.classList.add("hidden");
          messagesView.classList.remove("hidden");
        }
      }

      loginBtn.addEventListener("click", function(){
        var v = (passInput.value || "").trim();
        if (!v) {
          loginError.textContent = "Enter password.";
          return;
        }
        fetch("/admin/api/login", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({password: v})
        }).then(function(r){
          if (!r.ok) throw new Error("bad");
          return r.json();
        }).then(function(){
          loginError.textContent = "";
          adminPass = v;
          storePass(v);
          showApp();
        }).catch(function(){
          loginError.textContent = "Wrong password.";
        });
      });

      loadStoredPass();
      if (adminPass) {
        showApp();
      }

      tabUsers.addEventListener("click", function(){ activateTab("users"); });
      tabMessages.addEventListener("click", function(){ activateTab("messages"); });

      var filterUserId = document.getElementById("filter-user-id");
      var filterTelegramId = document.getElementById("filter-telegram-id");
      var usersTableWrap = document.getElementById("users-table-wrap");
      var loadUsersBtn = document.getElementById("load-users");
      loadUsersBtn.addEventListener("click", function(){
        if (!adminPass) {
          setStatus("Not logged in");
          return;
        }
        var params = [];
        var uid = (filterUserId.value || "").trim();
        var tid = (filterTelegramId.value || "").trim();
        if (uid) params.push("user_id=" + encodeURIComponent(uid));
        if (tid) params.push("telegram_id=" + encodeURIComponent(tid));
        var url = "/admin/api/users";
        if (params.length) url += "?" + params.join("&");
        setStatus("Loading users...");
        fetch(url, { headers: {"X-Admin-Password": adminPass} }).then(function(r){ return r.json(); }).then(function(data){
          setStatus("");
          renderUsers(data || {});
        }).catch(function(e){
          setStatus("Error loading users");
        });
      });

      function renderUsers(data) {
        var items = data.items || [];
        var html = "";
        html += "<table><thead><tr>";
        html += "<th>id</th><th>uid</th><th>telegram_id</th><th>username</th><th>display</th><th>locale</th><th>created_at</th>";
        html += "</tr></thead><tbody>";
        for (var i = 0; i < items.length; i++) {
          var u = items[i];
          html += "<tr>";
          html += "<td>" + (u.id || "") + "</td>";
          html += "<td>" + (u.uid || "") + "</td>";
          html += "<td>" + (u.telegram_id || "") + "</td>";
          html += "<td>" + (u.telegram_username || "") + "</td>";
          html += "<td>" + (u.display_name || "") + "</td>";
          html += "<td>" + (u.locale || "") + "</td>";
          html += "<td>" + (u.created_at || "") + "</td>";
          html += "</tr>";
        }
        html += "</tbody></table>";
        usersTableWrap.innerHTML = html;
        setSummary("Total: " + (data.total || items.length));
      }

      var msgUserId = document.getElementById("msg-user-id");
      var loadMessagesBtn = document.getElementById("load-messages");
      var messagesWrap = document.getElementById("messages-wrap");

      loadMessagesBtn.addEventListener("click", function(){
        if (!adminPass) {
          setStatus("Not logged in");
          return;
        }
        var uid = (msgUserId.value || "").trim();
        if (!uid) {
          messagesWrap.innerHTML = "<div class='muted'>Enter user id.</div>";
          return;
        }
        setStatus("Loading messages...");
        fetch("/admin/api/messages?user_id=" + encodeURIComponent(uid), { headers: {"X-Admin-Password": adminPass} }).then(function(r){ return r.json(); }).then(function(data){
          setStatus("");
          renderMessages(data || {});
        }).catch(function(){
          setStatus("Error loading messages");
        });
      });

      function renderMessages(data) {
        var items = data.items || [];
        var html = "";
        for (var i = 0; i < items.length; i++) {
          var m = items[i];
          html += "<div style='margin-bottom:8px;'>";
          html += "<div class='muted'>#" + m.id + " · " + m.role + " · " + (m.created_at || "") + "</div>";
          html += "<pre>" + (m.content || "") + "</pre>";
          html += "</div>";
        }
        if (!items.length) {
          html = "<div class='muted'>No messages.</div>";
        }
        messagesWrap.innerHTML = html;
        setSummary("Messages: " + (data.total || items.length));
      }
    })();
  </script>
</body>
</html>
"""
	return web.Response(text=html, content_type="text/html; charset=utf-8")


def _check_admin_auth(request: web.Request) -> bool:
	if not config.ADMIN_PASSWORD:
		return False
	h = request.headers.get("X-Admin-Password") or ""
	return bool(h and h == config.ADMIN_PASSWORD)


async def admin_login(request: web.Request) -> web.Response:
	if not config.ADMIN_PASSWORD:
		return web.json_response({"error": "ADMIN_PASSWORD not set on server"}, status=500)
	try:
		data = await request.json()
	except Exception:
		data = {}
	p = (data.get("password") or "").strip()
	if not p or p != config.ADMIN_PASSWORD:
		return web.json_response({"ok": False}, status=401)
	return web.json_response({"ok": True})


async def admin_users(request: web.Request) -> web.Response:
	if not _check_admin_auth(request):
		return web.json_response({"error": "unauthorized"}, status=401)
	limit = 100
	try:
		limit = max(1, min(500, int(request.query.get("limit") or "100")))
	except Exception:
		pass
	user_id = request.query.get("user_id")
	tg_id = request.query.get("telegram_id")
	where = []
	args = []
	if user_id:
		where.append("id = $%d" % (len(args) + 1))
		args.append(int(user_id))
	if tg_id:
		where.append("telegram_id = $%d" % (len(args) + 1))
		args.append(int(tg_id))
	where_sql = " where " + " and ".join(where) if where else ""
	sql_items = f"select id, uid, telegram_id, telegram_username, display_name, locale, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at from users{where_sql} order by id desc limit {limit}"
	sql_count = f"select count(*) from users{where_sql}"
	pool = get_pool()
	async with pool.acquire() as conn:
		rows = await conn.fetch(sql_items, *args)
		cnt_row = await conn.fetchrow(sql_count, *args)
	items = []
	for r in rows:
		items.append({
			"id": int(r["id"]),
			"uid": r["uid"],
			"telegram_id": int(r["telegram_id"]) if r["telegram_id"] is not None else None,
			"telegram_username": r["telegram_username"],
			"display_name": r["display_name"],
			"locale": r["locale"],
			"created_at": r["created_at"],
		})
	total = int(cnt_row["count"]) if cnt_row and "count" in cnt_row else len(items)
	return web.json_response({"items": items, "total": total})


async def admin_messages(request: web.Request) -> web.Response:
	if not _check_admin_auth(request):
		return web.json_response({"error": "unauthorized"}, status=401)
	try:
		user_id = int(request.query.get("user_id") or "0")
	except Exception:
		user_id = 0
	if not user_id:
		return web.json_response({"items": [], "total": 0})
	limit = 100
	try:
		limit = max(1, min(500, int(request.query.get("limit") or "100")))
	except Exception:
		pass
	pool = get_pool()
	async with pool.acquire() as conn:
		rows = await conn.fetch(
			"select id, role, content, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at from messages where user_id=$1 order by id desc limit $2",
			user_id,
			limit,
		)
	items = []
	for r in rows:
		items.append({
			"id": int(r["id"]),
			"role": r["role"],
			"content": r["content"],
			"created_at": r["created_at"],
		})
	return web.json_response({"items": items, "total": len(items)})

def register_routes(app: web.Application) -> None:
	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
	app.router.add_route("*", "/tg/pay/freekassa/notify", freekassa_notify)
	app.router.add_get("/tg/pay/success", freekassa_success)
	app.router.add_get("/tg/pay/fail", freekassa_fail)
	app.router.add_get("/admin", admin_page)
	app.router.add_post("/admin/api/login", admin_login)
	app.router.add_get("/admin/api/users", admin_users)
	app.router.add_get("/admin/api/messages", admin_messages)
