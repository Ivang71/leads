import os, asyncio


class TlsBrowser:
	def __init__(self, user_agent: str, proxy: str | None):
		self.user_agent = user_agent
		self.proxy = proxy
		try:
			import tls_client  # type: ignore
		except Exception as e:
			raise RuntimeError("tls-client python bindings are required. Install with: pip install tls-client") from e
		profile = os.environ.get("TLS_CLIENT_PROFILE", "chrome_140")
		self.session = tls_client.Session(
			client_identifier=profile,
			random_tls_extension_order=True,
		)

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb):
		await self.aclose()

	def close(self) -> None:
		try:
			close = getattr(self.session, 'close', None)
			if callable(close):
				close()
		except Exception:
			pass
		try:
			jar = getattr(self.session, 'cookies', None)
			clear = getattr(jar, 'clear', None)
			if callable(clear):
				clear()
		except Exception:
			pass

	async def aclose(self) -> None:
		try:
			await asyncio.to_thread(self.close)
		except Exception:
			pass

	def __del__(self):
		try:
			self.close()
		except Exception:
			pass

	async def _do(self, method: str, url: str, headers: dict, timeout: int, follow: bool, max_bytes: int | None = None) -> dict:
		h = dict(headers)
		h['User-Agent'] = self.user_agent
		kwargs = {
			'headers': h,
			'allow_redirects': follow,
			'timeout_seconds': timeout,
		}
		if self.proxy:
			kwargs['proxy'] = self.proxy
		try:
			MAX_BYTES = int(max_bytes if max_bytes is not None else int(os.environ.get('HTTP_MAX_BYTES', '8192')))
		except Exception:
			MAX_BYTES = 8192
		body = b''
		resp = None
		aborted_at_cap = False
		try:
			if follow:
				resp = await asyncio.to_thread(getattr(self.session, method), url, **kwargs)
				try:
					content = resp.content if getattr(resp, 'content', None) is not None else b''
				except Exception:
					content = b''
				try:
					body = bytes(content[:MAX_BYTES])
				except Exception:
					body = b''
				try:
					aborted_at_cap = isinstance(content, (bytes, bytearray)) and len(content) > MAX_BYTES
				except Exception:
					aborted_at_cap = False
				try:
					close = getattr(resp, 'close', None)
					if callable(close):
						close()
				except Exception:
					pass
			else:
				resp = await asyncio.to_thread(getattr(self.session, method), url, stream=True, **kwargs)
				try:
					iter_content = getattr(resp, 'iter_content', None)
					if callable(iter_content):
						for chunk in iter_content(chunk_size=2048):
							if not chunk:
								break
							remaining = MAX_BYTES - len(body)
							if remaining <= 0:
								aborted_at_cap = True
								break
							body += bytes(chunk[:remaining])
							if len(body) >= MAX_BYTES:
								aborted_at_cap = True
								break
						try:
							close = getattr(resp, 'close', None)
							if callable(close):
								close()
						except Exception:
							pass
					else:
						raise RuntimeError('streaming_not_supported')
			except Exception:
			resp = await asyncio.to_thread(getattr(self.session, method), url, **kwargs)
			try:
				content = resp.content if getattr(resp, 'content', None) is not None else b''
			except Exception:
				content = b''
			try:
				body = bytes(content[:MAX_BYTES])
			except Exception:
				body = b''
			try:
				aborted_at_cap = isinstance(content, (bytes, bytearray)) and len(content) > MAX_BYTES
			except Exception:
				aborted_at_cap = False
			try:
				close = getattr(resp, 'close', None)
				if callable(close):
					close()
			except Exception:
				pass
		try:
			final_url = str(resp.url)
		except Exception:
			final_url = url
		status = getattr(resp, 'status_code', None)
		return {'status': status, 'url': final_url, 'content': body, 'aborted_at_cap': aborted_at_cap}

	async def get(self, url: str, headers: dict, timeout: int = 10, follow: bool = False, max_bytes: int | None = None) -> dict:
		return await self._do('get', url, headers, timeout, follow=follow, max_bytes=max_bytes)


