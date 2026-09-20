"""Small HTML parsers for JAVDatabase pages; no browser or parser dependency."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

BASE_URL = "https://www.javdatabase.com/"
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_HIDDEN = {"script", "style", "noscript"}


class _Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def text(self):
        if self.tag in _HIDDEN:
            return ""
        return " ".join(
            child.text() if isinstance(child, _Node) else child
            for child in self.children
        )

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()


class _Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.current = self.root
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        # HTML allows omitted closing tags for paragraphs and list items.
        if tag in {"p", "li"} and self.current.tag == tag:
            self.current = self.current.parent
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def _text(node):
    return re.sub(r"\s+", " ", node.text()).strip()


def _url(value, base):
    if not value:
        return None
    resolved = urljoin(base, value.strip())
    return resolved if urlsplit(resolved).scheme in {"http", "https"} else None


def _image(node, base):
    for key in ("data-src", "data-lazy-src", "src"):
        value = _url(node.attrs.get(key), base)
        if value:
            return value
    return None


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def parse_search(html, base_url=BASE_URL):
    """Return ordered, deduplicated result dictionaries from a search page."""
    results, seen = [], set()
    for card in _Document(html).root.walk():
        if not card.has_class("borderlesscard"):
            continue
        nodes = list(card.walk())
        code_block = next((node for node in nodes if node.has_class("pcard")), None)
        if code_block is None:
            continue
        anchor = next(
            (
                node
                for node in code_block.walk()
                if node.tag == "a" and node.attrs.get("href")
            ),
            None,
        )
        if anchor is None:
            continue
        link = _url(anchor.attrs["href"], base_url)
        if (
            not link
            or link in seen
            or "sponsored" in (anchor.attrs.get("rel") or "").split()
        ):
            continue
        parsed_link = urlsplit(link)
        if parsed_link.hostname != urlsplit(base_url).hostname or not re.fullmatch(
            r"/(movies|uncensored)/[^/]+/", parsed_link.path
        ):
            continue
        seen.add(link)
        title_block = next((node for node in nodes if node.has_class("mt-auto")), None)
        title = (
            next(
                (_text(node) for node in title_block.walk() if node.tag == "a"),
                _text(anchor),
            )
            if title_block
            else _text(anchor)
        )
        poster = next(
            (
                _image(node, base_url)
                for node in nodes
                if node.tag == "img" and _image(node, base_url)
            ),
            None,
        )
        studio = next(
            (
                _text(node)
                for node in nodes
                if node.has_class("btn") or node.has_class("btn-primary")
            ),
            None,
        )
        date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", _text(card))
        results.append(
            {
                "code": _text(anchor),
                "title": title,
                "link": link,
                "date": date.group() if date else None,
                "studio": studio,
                "poster": poster,
            }
        )
    return results


def _tokens(node):
    for child in node.children:
        if isinstance(child, str) or child.tag in {"b", "strong", "a", "br"}:
            yield child
        elif child.tag not in _HIDDEN:
            yield from _tokens(child)


def _fields(root):
    fields = {}
    for label in root.walk():
        if label.tag not in {"b", "strong"}:
            continue
        key = _text(label).rstrip(": ").casefold()
        container = label.parent
        while container.parent is not None and container.tag not in {
            "p",
            "div",
            "li",
            "td",
            "th",
        }:
            container = container.parent
        if container.tag in {"td", "th"} and container.parent.tag == "tr":
            container = container.parent
        tokens = list(_tokens(container))
        if label not in tokens:
            continue
        values, links = [], []
        for token in tokens[tokens.index(label) + 1 :]:
            if isinstance(token, _Node):
                if token.tag in {"b", "strong"}:
                    break
                value = _text(token)
                if token.tag == "a":
                    links.append(value)
            else:
                value = token
            values.append(value)
        value = re.sub(r"\s+", " ", " ".join(values)).strip(" :–-")
        if value:
            fields.setdefault(key, (value, _unique(links)))
    return fields


def _plot_tokens(node):
    if node.tag in _HIDDEN:
        return
    if (
        re.fullmatch(r"h[1-6]", node.tag)
        or node.has_class("wp-postratings")
        or node.has_class("tablelabel")
    ):
        yield node
        return
    block = node.tag in {"p", "div", "br", "li"}
    if block:
        yield "\n"
    for child in node.children:
        if isinstance(child, str):
            yield child
        else:
            yield from _plot_tokens(child)
    if block:
        yield "\n"


def _plot(root):
    active, parts = False, []
    for token in _plot_tokens(root):
        if isinstance(token, _Node):
            if active:
                break
            active = bool(re.fullmatch(r"h[1-6]", token.tag)) and bool(
                re.search(r"\bAbout\b.*\bJAV Movie\b", _text(token), re.IGNORECASE)
            )
        elif active:
            parts.append(token)
    text = "".join(parts)
    text = re.split(
        r"\(?No Ratings Yet|Loading\.{2,}|JAV Database only provides",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n\n".join(line for line in lines if line) or None


def parse_movie(html, page_url=BASE_URL):
    """Extract a JSON-compatible movie dictionary; missing scalars are None."""
    root = _Document(html).root
    nodes = list(root.walk())
    fields = _fields(root)

    def field(*labels):
        return next((fields[label][0] for label in labels if label in fields), None)

    def names(*labels):
        for label in labels:
            if label in fields:
                value, links = fields[label]
                return links or _unique(
                    part.strip() for part in re.split(r"[,;|]", value)
                )
        return []

    title = field("title") or next(
        (_text(node) for node in nodes if node.tag == "h1"), None
    )
    poster = None
    for node in nodes:
        if node.attrs.get("id") == "poster-container" or node.has_class("poster"):
            poster = next(
                (
                    _image(image, page_url)
                    for image in node.walk()
                    if image.tag == "img"
                ),
                None,
            )
            if poster:
                break
    if not poster:
        poster = next(
            (
                _image(node, page_url)
                for node in nodes
                if node.tag == "img"
                and "jav movie cover" in (node.attrs.get("alt") or "").casefold()
            ),
            None,
        )
    video = next(
        (
            node
            for node in nodes
            if node.tag == "video" and node.attrs.get("id") == "jav-player"
        ),
        None,
    )
    trailer = None
    if video:
        poster = poster or _url(video.attrs.get("poster"), page_url)
        trailer = _url(video.attrs.get("src"), page_url) or next(
            (
                _url(node.attrs.get("src"), page_url)
                for node in video.walk()
                if node.tag == "source" and _url(node.attrs.get("src"), page_url)
            ),
            None,
        )
    if not poster:
        poster = next(
            (
                _url(node.attrs.get("content"), page_url)
                for node in nodes
                if node.tag == "meta" and node.attrs.get("property") == "og:image"
            ),
            None,
        )

    previews = _unique(
        _url(
            node.attrs.get("data-image-href") or node.attrs.get("data-image-src"),
            page_url,
        )
        for node in nodes
        if node.attrs.get("data-image-href") or node.attrs.get("data-image-src")
    )

    actresses = names(
        "idol(s)/actress(es)", "idol(s)", "idols", "idol", "actress(es)", "actresses"
    )
    if not actresses:
        for node in nodes:
            if re.fullmatch(r"h[1-6]", node.tag) and "Actress/Idols" in _text(node):
                actresses = _unique(
                    _text(link)
                    for link in node.parent.walk()
                    if link.tag == "a"
                    and re.fullmatch(
                        r"/idols/[^/]+/", urlsplit(link.attrs.get("href") or "").path
                    )
                )
                break
    rating = None
    for node in nodes:
        if node.has_class("wp-postratings"):
            match = re.search(
                r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s+from\s+([\d,]+)\s+votes?",
                _text(node),
                re.IGNORECASE,
            )
            if match:
                value, maximum, votes = (
                    float(match[1]),
                    float(match[2]),
                    int(match[3].replace(",", "")),
                )
                if 0 <= value <= maximum and maximum > 0 and votes > 0:
                    rating = {"value": value, "max": maximum, "votes": votes}
            break

    return {
        "link": page_url,
        "title": title,
        "series": field("jav series", "series"),
        "dvd_id": field("dvd id", "dvd"),
        "content_id": field("content id"),
        "release_date": field("release date", "released", "release"),
        "runtime": field("runtime", "duration"),
        "studio": field("studio"),
        "director": field("director"),
        "plot": _plot(root),
        "genres": names("genre(s)", "genres", "genre"),
        "actresses": actresses,
        "trailer": trailer,
        "rating": rating,
        "preview_images": previews,
        "poster": poster,
    }
