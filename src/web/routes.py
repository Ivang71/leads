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
	app.router.add_post("/admin/api/login", admin_login)
	app.router.add_get("/admin/api/users", admin_users)
	app.router.add_get("/admin/api/messages", admin_messages)
