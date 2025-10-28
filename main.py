import os, sys, re, asyncio, json, traceback
from urllib.parse import urlparse
from dotenv import load_dotenv
from importlib import import_module
from bs4 import BeautifulSoup, Comment
from groq import Groq
import tiktoken


load_dotenv()

QUERY = "сдэк директор отдела продаж"
TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200

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
print(f"DBG links={len(links)}")

def _strip_html_to_text(html_bytes: bytes) -> str:
    soup = BeautifulSoup(html_bytes, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "picture", "source", "template", "iframe"]):
        t.decompose()
    for sel in ["header", "footer", "nav", "aside", "form", "menu"]:
        for t in soup.select(sel):
            t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    rm_re = re.compile(r"(nav|menu|breadcrumb|footer|header|social|subscribe|comment|related|sidebar|cookie|banner|ad|promo|partner|catalog|search|rating|license|policy)", re.I)
    for t in soup.find_all(True):
        try:
            attrs = getattr(t, 'attrs', None)
            if not isinstance(attrs, dict):
                continue
            id_val = attrs.get('id')
            classes = attrs.get('class') or []
            if isinstance(classes, str):
                classes = [classes]
            ident = " ".join([
                str(id_val or ""),
                " ".join([str(c) for c in classes])
            ]).strip()
            if ident and rm_re.search(ident):
                t.decompose()
        except Exception:
            continue
    text = (soup.body or soup).get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    email_re = re.compile(r"[\w\.-]+@[\w\.-]+\.[A-Za-zА-Яа-я]{2,}")
    phone_re = re.compile(r"\+?\d[\d\-\s\(\)]{8,}\d")
    drop_re = re.compile(r"^(Поиск|Главное|Новости|Публикации|Интервью|Спецпроекты|Подкасты|Афиша|RSS-новости|Рейтинги|Категории|Каталог|Кейсы|Ещё|Maps|Market|Contacts|Контакты|Мы в социальных сетях:|Подписывайтесь|Мы на связи|На главную|Этот сайт использует cookie|Политика конфиденциальности|Пользовательское соглашение|Положение об обработке персональных данных|Согласие на обработку персональных данных|©|Telegram|ВКонтакте|Одноклассники|Rutube|Все рейтинги|Лидеры рейтингов|Календарь событий)\b", re.I)
    kept = []
    seen = set()
    for ln in lines:
        if not ln:
            continue
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        if drop_re.search(ln):
            continue
        if len(ln.split()) < 3 and not (email_re.search(ln) or phone_re.search(ln)):
            continue
        kept.append(ln)
    return "\n".join(kept)

def _trim_to_token_limit(instruction_prefix: str, text: str, token_limit: int, safety: int) -> tuple[str, dict]:
    enc = tiktoken.get_encoding("cl100k_base")
    pref_tokens = enc.encode(instruction_prefix or "")
    text_tokens = enc.encode(text or "")
    budget = max(0, token_limit - len(pref_tokens) - max(0, safety))
    keep = min(len(text_tokens), budget)
    trimmed_text = enc.decode(text_tokens[:keep]) if keep > 0 else ""
    return trimmed_text, {
        "pref_tokens": len(pref_tokens),
        "text_tokens": len(text_tokens),
        "budget": budget,
        "kept": keep,
        "total_after": len(pref_tokens) + keep,
    }

async def fetch_all() -> None:
    _extend_sys_path()
    TlsBrowser = import_module("phantom.net.client").TlsBrowser
    generate_user_agent = import_module("phantom.browser.ua").generate_user_agent
    ua, _ = generate_user_agent(0)
    print(f"DBG ua={ua}")
    query = QUERY
    print(f"DBG query={query}")
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    out_dir = os.path.join(os.path.dirname(__file__), "htmls")
    os.makedirs(out_dir, exist_ok=True)

    sem = asyncio.Semaphore(50)
    print("DBG concurrency=50")
    written_txts = []

    async def fetch_one(i: int, url: str) -> None:
        async with sem:
            try:
                print(f"DBG start i={i} url={url}")
                async with TlsBrowser(user_agent=ua, proxy=None) as browser:
                    res = await browser.get(url, headers=headers, timeout=8, follow=True, max_bytes=16777216)
                res_dict = res if isinstance(res, dict) else {}
                if not res_dict:
                    print(f"DBG no_res_dict i={i} url={url} type={type(res)}")
                status = res_dict.get("status")
                final_url = res_dict.get("url") or url
                content_val = res_dict.get("content")
                body = content_val if isinstance(content_val, (bytes, bytearray)) else (bytes(content_val or b"") if content_val is not None else b"")
                headers_dict = res_dict.get("headers") or {}
                ctype = headers_dict.get("content-type") or headers_dict.get("Content-Type")
                print(f"DBG resp i={i} status={status} final={final_url} len={len(body)} ctype={ctype}")
                p = urlparse(final_url)
                host = (p.netloc or "site").replace(":", "_")
                path_part = (p.path or "/").strip("/").replace("/", "_")
                if not path_part:
                    path_part = "index"
                if len(path_part) > 80:
                    path_part = path_part[:80]
                base = f"{i+1:02d}_{host}_{path_part}"
                html_path = os.path.join(out_dir, base + ".html")
                with open(html_path, "wb") as wf:
                    wf.write(body)
                if body:
                    try:
                        txt = _strip_html_to_text(body)
                    except Exception:
                        print(f"DBG strip_fail i={i} url={final_url}\n{traceback.format_exc()}")
                        txt = ""
                else:
                    print(f"DBG empty_body i={i} url={final_url}")
                    txt = ""
                txt_path = os.path.join(out_dir, base + ".txt")
                with open(txt_path, "w", encoding="utf-8") as tf:
                    tf.write(txt)
                print(f"{status} {final_url} -> {html_path} | {txt_path} bytes={len(body)}")
                try:
                    written_txts.append((i, txt_path))
                except Exception:
                    pass
            except Exception as e:
                print(f"ERR {url} {e}\n{traceback.format_exc()}")

    tasks = [asyncio.create_task(fetch_one(i, url)) for i, url in enumerate(links)]
    if tasks:
        await asyncio.gather(*tasks)
    try:
        combined_path = os.path.join(out_dir, "_combined.txt")
        parts = []
        for _, pth in sorted(written_txts, key=lambda x: x[0]):
            try:
                with open(pth, "r", encoding="utf-8") as rf:
                    content = rf.read().strip()
                    if content:
                        parts.append(content)
            except Exception as e:
                print(f"DBG combine_fail path={pth} err={e}")
        with open(combined_path, "w", encoding="utf-8") as wf:
            wf.write("\n\n-----\n\n".join(parts))
        print(f"DBG combined -> {combined_path} docs={len(parts)}")
    except Exception as e:
        print(f"DBG combine_error {e}\n{traceback.format_exc()}")
    # Send to LLM
    try:
        with open(combined_path, "r", encoding="utf-8") as rf:
            combined_text = rf.read()
        system_prompt = (
            "Ты помощник-экстрактор фактов. Отвечай строго одним полным именем в именительном падеже."
            " Никаких дополнительных слов, знаков или комментариев. Если данных недостаточно — выдай пустую строку."
        )
        prefix = (
            "В следующем тексте твоя задача выдать наиболее актуальную информацию, отвечающую кто сейчас \"{q}\". "
            "Отвечай одним полным именем, например \"Иван Остапович Озёрный\" или \"Екатерина Васильевна Глухих\". "
            "Ты не можешь вставить в ответ ничего кроме одного единственного полного имени. "
            "Информация с более поздней датой имеет колоссальный приоритет.\n\n"
            "Текст:\n"
        ).format(q=query)
        trimmed_text, st = _trim_to_token_limit(prefix, combined_text, TOKEN_LIMIT, SAFETY_TOKENS)
        print(f"DBG tokens pref={st['pref_tokens']} text={st['text_tokens']} budget={st['budget']} kept={st['kept']} total_after={st['total_after']} limit={TOKEN_LIMIT}")
        user_prompt = prefix + trimmed_text
        # Final cap by characters removed; token-based trimming enforces limit
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=64,
            top_p=1,
            stream=False,
        )
        answer = (resp.choices[0].message.content or "").strip()
        answer_path = os.path.join(out_dir, "_answer.txt")
        with open(answer_path, "w", encoding="utf-8") as wf:
            wf.write(answer)
        print(f"DBG llm_answer -> {answer_path}: {answer}")
    except Exception as e:
        print(f"DBG llm_error {e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(fetch_all())

