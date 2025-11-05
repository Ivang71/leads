import sys, json, base64, asyncio
from tls_browser import TlsBrowser

async def run(url: str, timeout: int) -> None:
	headers = {
		"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
	}
	ua = "Mozilla/5.0"
	async with TlsBrowser(user_agent=ua, proxy=None) as browser:
		res = await browser.get(url, headers=headers, timeout=timeout, follow=True, max_bytes=16777216)
	final_url = (res or {}).get("url") or url
	content_val = (res or {}).get("content")
	body = content_val if isinstance(content_val, (bytes, bytearray)) else (bytes(content_val or b"") if content_val is not None else b"")
	print(json.dumps({"url": final_url, "content": base64.b64encode(body).decode("ascii")}))

if __name__ == "__main__":
	try:
		url = sys.argv[1]
		timeout = int(float(sys.argv[2])) if len(sys.argv) > 2 else 8
		asyncio.run(run(url, timeout))
	except Exception as e:
		sys.stderr.write(str(e) + "\n")
		sys.exit(1)
