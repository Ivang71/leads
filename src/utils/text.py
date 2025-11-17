def split_telegram_messages(text: str, limit: int = 4096) -> list[str]:
	if not text:
		return []
	out = []
	buf = []
	curr = 0
	for ln in text.splitlines(keepends=True):
		if len(ln) > limit:
			if curr > 0:
				out.append("".join(buf))
				buf = []
				curr = 0
			for i in range(0, len(ln), limit):
				out.append(ln[i:i+limit])
			continue
		if curr + len(ln) > limit:
			out.append("".join(buf))
			buf = []
			curr = 0
		buf.append(ln)
		curr += len(ln)
	if curr > 0:
		out.append("".join(buf))
	return out
