import os, sys, asyncio, json
from urllib.parse import urlparse
from dotenv import load_dotenv
from importlib import import_module


load_dotenv()

with open("mock.json", encoding="utf-8") as f:
    obj = json.load(f)

def _extend_sys_path():
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if base not in sys.path:
        sys.path.insert(0, base)

def _extract_links(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "link" and isinstance(v, str):
                yield v
            else:
                yield from _extract_links(v)
    elif isinstance(o, list):
        for it in o:
            yield from _extract_links(it)

links = list(dict.fromkeys(list(_extract_links(obj))))

async def fetch_all() -> None:
    _extend_sys_path()
    TlsBrowser = import_module("phantom.net.client").TlsBrowser
    generate_user_agent = import_module("phantom.browser.ua").generate_user_agent
    ua, _ = generate_user_agent(0)
    proxy = os.getenv("HTTP_PROXY") or os.getenv("PROXY")
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    out_dir = os.path.join(os.path.dirname(__file__), "htmls")
    os.makedirs(out_dir, exist_ok=True)
    async with TlsBrowser(user_agent=ua, proxy=proxy) as browser:
        for i, url in enumerate(links):
            try:
                res = await browser.get(url, headers=headers, timeout=30, follow=True, max_bytes=16777216)
                status = res.get("status")
                final_url = res.get("url", url)
                body = res.get("content", b"") or b""
                p = urlparse(final_url)
                host = (p.netloc or "site").replace(":", "_")
                path_part = (p.path or "/").strip("/").replace("/", "_")
                if not path_part:
                    path_part = "index"
                if len(path_part) > 80:
                    path_part = path_part[:80]
                fname = f"{i+1:02d}_{host}_{path_part}.html"
                fpath = os.path.join(out_dir, fname)
                with open(fpath, "wb") as wf:
                    wf.write(body)
                print(f"{status} {final_url} -> {fpath} bytes={len(body)}")
            except Exception as e:
                print(f"ERR {url} {e}")

if __name__ == "__main__":
    asyncio.run(fetch_all())

