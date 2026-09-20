"""HTTP access with bounded retries and streamed, atomic downloads."""

import math
import time
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .export import atomic_file
from .parsing import BASE_URL, parse_movie, parse_search


class JavDBError(Exception):
    """A request failed or a page did not contain recognizable movie metadata."""


class Client:
    """Fetch JAVDatabase metadata. Retries apply to connection failures and 429/5xx."""

    def __init__(self, timeout=15, retries=2):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and greater than zero")
        if not isinstance(retries, int) or retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        self.timeout = timeout
        self.retries = retries

    def _request(self, url, consume):
        if urlsplit(url).scheme not in {"http", "https"}:
            raise JavDBError("Only HTTP and HTTPS URLs are supported")
        request = Request(
            url,
            headers={"User-Agent": "javdb-python/0.2.0", "Accept-Encoding": "identity"},
        )
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return consume(response)
            except HTTPError as error:
                error.close()
                retryable = error.code == 429 or 500 <= error.code < 600
                message = f"HTTP {error.code}"
            except (URLError, TimeoutError, ConnectionError, HTTPException) as error:
                retryable = True
                message = str(error)
            if not retryable or attempt == self.retries:
                raise JavDBError(f"Failed to fetch {url}: {message}")
            time.sleep(min(2**attempt, 8))

    def _html(self, url):
        def read(response):
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(encoding, errors="replace")

        return self._request(url, read)

    def search(self, query, page=1, pages=1):
        """Search one or more consecutive pages, preserving site order."""
        if not query.strip():
            raise ValueError("query cannot be empty")
        if (
            not isinstance(page, int)
            or not isinstance(pages, int)
            or page < 1
            or pages < 1
        ):
            raise ValueError("page and pages must be positive integers")
        results, seen = [], set()
        for number in range(page, page + pages):
            url = (
                BASE_URL
                + "?"
                + urlencode(
                    {"post_type": "movies,uncensored", "s": query, "paged": number}
                )
            )
            batch = parse_search(self._html(url), BASE_URL)
            fresh = [result for result in batch if result["link"] not in seen]
            if not fresh:
                break
            for result in fresh:
                seen.add(result["link"])
                results.append(result)
        return results

    def movie(self, url):
        """Fetch and parse a movie in one request, raising on unrecognized pages."""
        movie = parse_movie(self._html(url), url)
        identified = movie["dvd_id"] or movie["content_id"]
        detailed = movie["studio"] and movie["release_date"] and movie["runtime"]
        if not movie["title"] or not (identified or detailed):
            raise JavDBError(
                f"No movie metadata found at {url} (page changed or access blocked)"
            )
        return movie

    def download(self, url, destination, overwrite=False):
        """Stream an image to disk; an interrupted download leaves no partial file."""

        def save(response):
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise JavDBError(
                    f"Expected an image from {url}; received {content_type}"
                )
            with atomic_file(destination, overwrite=overwrite) as output:
                size = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    size += len(chunk)
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and size != int(length):
                    raise IncompleteRead(b"", max(0, int(length) - size))
                if not size:
                    raise JavDBError(f"Empty image from {url}")

        self._request(url, save)
