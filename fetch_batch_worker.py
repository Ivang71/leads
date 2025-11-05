import sys, json, base64, asyncio
from tls_browser import TlsBrowser

async def run(cfg: dict) -> None:
	urls = cfg.get("urls") or []
	concurrency = int(cfg.get("concurrency") or 10)
	per_url_timeout = int(cfg.get("per_url_timeout") or 6)
	max_bytes = int(cfg.get("max_bytes") or 16777216)
	headers = {
		"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
	}
	ua = "Mozilla/5.0"
	sem = asyncio.Semaphore(concurrency)

	async with TlsBrowser(user_agent=ua, proxy=None) as browser:
		async def one(i: int, url: str) -> None:
			async with sem:
				status = "ok"
				final_url = url
				body = b""
				try:
					res = await asyncio.wait_for(
						browser.get(url, headers=headers, timeout=per_url_timeout, follow=True, max_bytes=max_bytes),
						timeout=per_url_timeout + 1,
					)
					final_url = (res or {}).get("url") or url
					content_val = (res or {}).get("content")
					body = content_val if isinstance(content_val, (bytes, bytearray)) else (bytes(content_val or b"") if content_val is not None else b"")
				except asyncio.TimeoutError:
					status = "timeout"
				except Exception:
					status = "fail"
				content_b64 = base64.b64encode(body).decode("ascii") if body else ""
				header = {
					"i": i,
					"url": url,
					"final_url": final_url,
					"status": status,
					"content_len": len(content_b64),
				}
				print(json.dumps(header), flush=True)
				if content_b64:
					sys.stdout.write(content_b64 + "\n")
					sys.stdout.flush()
		await asyncio.gather(*[one(i, u) for i, u in enumerate(urls)])

if __name__ == "__main__":
	cfg_text = sys.stdin.read()
	try:
		cfg = json.loads(cfg_text)
	except Exception:
		sys.exit(2)
	asyncio.run(run(cfg))
