import time, logging
from aiohttp import web
from ..clients.alice import ask_alice
from ..llm.extract import extract_name_with_groq, format_extracted_name
from ..stats import record_request_stat

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
		ms_text = await ask_alice(q)
	except Exception as e:
		logging.exception("test failed")
		resp = web.json_response({
			"error": "processing failed",
			"reason": str(e),
		}, status=500)
		await record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": len(q)})
		return resp
	if not ms_text:
		resp = web.json_response({"error": "no answer produced"}, status=502)
		await record_request_stat({
			"ts": int(time.time()),
			"source": "http_test",
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok,
			"q_len": len(q),
			"ms_len": 0,
			"out_len": 0,
		})
		return resp
	name = extract_name_with_groq(q, ms_text)
	out = (format_extracted_name(name) or (ms_text or "").strip())
	if not out:
		resp = web.json_response({"error": "no answer produced"}, status=502)
		await record_request_stat({
			"ts": int(time.time()),
			"source": "http_test",
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok,
			"q_len": len(q),
			"ms_len": len((ms_text or "").strip()),
			"out_len": 0,
		})
		return resp
	ok = True
	resp = web.Response(text=out)
	await record_request_stat({
		"ts": int(time.time()),
		"source": "http_test",
		"dur": round(max(0.0, time.monotonic() - t0), 1),
		"ok": ok,
		"q_len": len(q),
		"ms_len": len((ms_text or "").strip()),
		"out_len": len(out),
	})
	return resp

def register_routes(app: web.Application) -> None:
	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
