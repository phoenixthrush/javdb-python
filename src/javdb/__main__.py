import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring

import niquests

REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 20
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".ts", ".webm"}


def _clean_html_text(fragment: str) -> str:
    """Turn a small HTML fragment into one clean line."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to text while keeping useful block boundaries."""
    html = re.sub(
        r"<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _fetch_html(url: str) -> Optional[str]:
    """Fetch optional movie details without crashing the whole command."""
    try:
        response = niquests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text or ""
    except Exception as error:
        print(f"Failed to fetch {url}: {error}", file=sys.stderr)
        return None


def _extract_about(html: str) -> Optional[str]:
    heading = re.search(
        r"(?is)<h[1-6][^>]*>[^<]*About[^<]*JAV Movie[^<]*</h[1-6]>",
        html,
    )

    if heading:
        section = html[heading.end() :]
    else:
        fallback = re.search(r"(?is)About[^<]*JAV Movie(.*)", html)
        if not fallback:
            return None
        section = fallback.group(1)

    section = re.split(r"(?is)<h[1-6][^>]*>", section, maxsplit=1)[0]
    text = _html_to_text(section)

    # These are page widgets and notices, not part of the plot.
    for marker in (
        r"\(No Ratings Yet\).*",
        r"No Ratings Yet.*",
        r"Loading\.{0,3}.*",
        r"JAV Database only provides official, legitimate & legal links.*",
    ):
        text = re.sub(marker, "", text)

    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text or None


def _labeled_links(html: str, label: str):
    """Return link text found after a bold field label."""
    pattern = re.compile(
        rf"(?is)<(?:p|div|li)[^>]*>[^<]*<b[^>]*>[^<]*"
        rf"{re.escape(label)}[^<]*</b>(.*?)</(?:p|div|li)>"
    )

    values = []
    for match in pattern.finditer(html):
        links = re.findall(r"<a[^>]*>(.*?)</a>", match.group(1), flags=re.I | re.S)
        for link in links:
            text = _clean_html_text(link)
            if text:
                values.append(text)
    return values


def _labeled_single(html: str, label: str) -> Optional[str]:
    """Return the first value after a bold field label."""
    pattern = re.compile(
        rf"(?is)<(?:p|div|li)[^>]*>\s*<b[^>]*>[^<]*"
        rf"{re.escape(label)}[^<]*</b>\s*[:\-–]?\s*"
        rf"(.*?)</(?:p|div|li)>"
    )
    match = pattern.search(html)
    if not match:
        return None

    # Stop before another bold label so fields cannot consume each other.
    block = re.split(r"<b[^>]*>.*?</b>", match.group(1), maxsplit=1)[0]
    block = re.sub(r"(?is)<br\s*/?>", "\n", block)
    first_line = next((line for line in block.splitlines() if line.strip()), "")
    return _clean_html_text(first_line) or None


def _extract_line(page_text: str, labels, max_words: int) -> Optional[str]:
    """Fallback for fields without the expected HTML wrapper."""
    for label in labels:
        pattern = re.compile(
            rf"{re.escape(label)}\s*[:\-–]?\s*(.*?)\s*(?:\n|$)",
            re.IGNORECASE,
        )
        match = pattern.search(page_text)
        if match:
            words = re.sub(r"\s{2,}", " ", match.group(1).strip()).split()
            return " ".join(words[:max_words])
    return None


def fetch_search(query: str):
    """Search JAVDatabase and return its visible result cards."""
    response = niquests.get(
        "https://www.javdatabase.com/",
        params={"post_type": "movies,uncensored", "s": query},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    html = response.text or ""

    card_pattern = re.compile(
        r'(?is)<div[^>]+class="[^"]*\bcard\b[^"]*\bborderlesscard\b[^"]*"'
        r"[^>]*>(.*?)</div>"
    )
    results = []

    for card in card_pattern.finditer(html):
        block = card.group(1)
        code_link = re.search(
            r'(?is)<p[^>]+class="[^"]*\bpcard\b[^"]*"[^>]*>.*?'
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            block,
        )
        if not code_link:
            continue

        link = code_link.group(1)
        code = _clean_html_text(code_link.group(2))

        title = None
        title_block = re.search(
            r'(?is)<(?:div|p|span)[^>]+class="[^"]*\bmt-auto\b[^"]*"'
            r"[^>]*>(.*?)</(?:div|p|span)>",
            block,
        )
        if title_block:
            title_link = re.search(r"(?is)<a[^>]*>(.*?)</a>", title_block.group(1))
            if title_link:
                title = _clean_html_text(title_link.group(1))

        text = _clean_html_text(block)
        date = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        studio = re.search(
            r'(?is)<span[^>]+class="[^"]*\bbtn(?:-primary)?\b[^"]*"'
            r"[^>]*>.*?<a[^>]*>(.*?)</a>",
            block,
        )

        results.append(
            {
                "code": code,
                "title": title or code,
                "link": link,
                "date": date.group(1) if date else None,
                "studio": _clean_html_text(studio.group(1)) if studio else None,
            }
        )

    return results


def parse_preview_images(html: str):
    """Extract preview and full-size gallery image URLs."""
    anchors = re.compile(r'(?is)<a([^>]*data-image-src="[^"]+"[^>]*)>(.*?)</a>')
    images = []

    for attributes, inner_html in anchors.findall(html):
        preview = re.search(r'data-image-src="([^"]+)"', attributes, flags=re.I)
        full = re.search(r'data-image-href="([^"]+)"', attributes, flags=re.I)
        image = re.search(r'<img[^>]+src="([^"]+)"', inner_html, flags=re.I)

        item = {
            "preview": preview.group(1) if preview else None,
            "full": full.group(1) if full else None,
            "img": image.group(1) if image else None,
        }
        if item["preview"] or item["full"]:
            images.append(item)

    return images


def parse_poster_url(html: str) -> Optional[str]:
    """Extract the best available poster URL."""
    container = re.search(
        r'(?is)<div[^>]+id="poster-container"[^>]*>(.*?)</div>',
        html,
    )
    if container:
        image = re.search(r'<img[^>]+src="([^"]+)"', container.group(1), flags=re.I)
        if image:
            return image.group(1)

    for pattern in (
        r'(?is)<div[^>]+class="[^"]*\bposter\b[^"]*"[^>]*>.*?'
        r'<img[^>]+src="([^"]+)"',
        r'<img[^>]+alt="[^"]*JAV Movie Cover[^"]*"[^>]*src="([^"]+)"',
    ):
        image = re.search(pattern, html, flags=re.I)
        if image:
            return image.group(1)
    return None


def parse_movie_metadata(html: str):
    """Extract Kodi-friendly metadata from one movie page."""
    title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html)
    page_text = _html_to_text(html)

    def field(label: str, fallbacks, max_words: int) -> Optional[str]:
        return _labeled_single(html, label) or _extract_line(
            page_text, fallbacks, max_words
        )

    genres = sorted(set(_labeled_links(html, "Genre")))
    actresses = sorted(set(_labeled_links(html, "Idol")))

    return {
        "Title": _clean_html_text(title.group(1)) if title else None,
        "DVD ID": field("DVD ID", ("DVD ID", "DVD"), 4),
        "Content ID": field("Content ID", ("Content ID",), 4),
        "Release Date": field("Release Date", ("Released",), 4),
        "Runtime": field("Runtime", ("Runtime",), 8),
        "Studio": field("Studio", ("Studio",), 8),
        "Director": field("Director", ("Director",), 8),
        "Series": field("Series", ("Series",), 8),
        "Plot": _extract_about(html),
        "Genre(s)": ", ".join(genres) if genres else None,
        "Idol(s)/Actress(es)": ", ".join(actresses) if actresses else None,
    }


def fetch_movie_details(page_url: str):
    """Fetch a movie once and parse all metadata and artwork."""
    html = _fetch_html(page_url)
    if html is None:
        return {}, None, []
    return (
        parse_movie_metadata(html),
        parse_poster_url(html),
        parse_preview_images(html),
    )


# Keep the original helpers available for small scripts using this module.
def fetch_preview_images(page_url: str):
    html = _fetch_html(page_url)
    return parse_preview_images(html) if html is not None else []


def fetch_poster_url(page_url: str) -> Optional[str]:
    html = _fetch_html(page_url)
    return parse_poster_url(html) if html is not None else None


def fetch_movie_metadata(page_url: str):
    html = _fetch_html(page_url)
    return parse_movie_metadata(html) if html is not None else {}


def safe_filename(name: str) -> str:
    """Create a short filename valid on common operating systems."""
    name = re.sub(r'[\x00-\x1f\\/:*?"<>|]+', "-", name.strip())
    return re.sub(r"\s+", "_", name).strip("._-")[:200]


def _select_nfo_basename(folder, dvd_id: Optional[str] = None) -> Optional[str]:
    """Prefer a matching video filename for the NFO filename."""
    try:
        files = sorted(
            (
                item
                for item in Path(folder).iterdir()
                if item.is_file() and not item.name.startswith(".")
            ),
            key=lambda item: item.name.casefold(),
        )
    except FileNotFoundError:
        return None

    if dvd_id:
        matches = [item for item in files if dvd_id.casefold() in item.name.casefold()]
        if matches:
            files = matches

    videos = [item for item in files if item.suffix.lower() in VIDEO_EXTENSIONS]
    if videos:
        return videos[0].stem
    if dvd_id or not files:
        return None
    return files[0].stem


def _split_values(value: Optional[str], ignored=()):
    if not value:
        return []

    ignored_names = {name.casefold() for name in ignored}
    values = []
    for item in re.split(r"[,|/;]+", value):
        item = item.strip()
        if item and item.casefold() not in ignored_names:
            values.append(item)
    return values


def _url_filename(url: str, fallback: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    return safe_filename(name) or fallback


def _download_file(url: str, destination: Path, label: str) -> bool:
    try:
        response = niquests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()

        with destination.open("wb") as output:
            for chunk in response.iter_content(8192):
                if chunk:
                    output.write(chunk)

        print(f"{label} downloaded -> {destination}")
        return True
    except Exception as error:
        print(f"{label} download failed: {error}", file=sys.stderr)
        return False


def _prepare_download_folder(metadata, title: str):
    """Choose the movie folder and default NFO basename."""
    folder_name = metadata.get("DVD ID") or title or "movie"
    safe_name = safe_filename(folder_name) or "movie"
    dvd_id = metadata.get("DVD ID")

    current_folder = Path.cwd()
    existing_base = _select_nfo_basename(current_folder, dvd_id)
    if existing_base:
        return current_folder, existing_base

    folder = Path(safe_name)
    folder.mkdir(parents=True, exist_ok=True)
    default_base = _select_nfo_basename(folder, dvd_id) or safe_name
    return folder, default_base


def _download_artwork(folder: Path, poster_url: Optional[str], previews):
    preview_folder = folder / "preview"
    preview_folder.mkdir(parents=True, exist_ok=True)
    (preview_folder / ".ignore").touch(exist_ok=True)

    if poster_url:
        poster_name = _url_filename(poster_url, "poster.jpg")
        _download_file(poster_url, preview_folder / poster_name, "Poster")

    local_fanart = []
    for number, image in enumerate(previews, start=1):
        url = image.get("full") or image.get("preview")
        if not url:
            continue

        filename = _url_filename(url, f"preview-{number}.jpg")
        if _download_file(url, preview_folder / filename, "Preview"):
            local_fanart.append(f"preview/{filename}")
    return local_fanart


def _build_json(selected, metadata, genres, actresses, previews, poster_url):
    preview_urls = []
    for image in previews:
        url = image.get("full") or image.get("preview")
        if url:
            preview_urls.append(url)

    return {
        "link": selected["link"],
        "title": metadata.get("Title") or selected.get("title"),
        "jav_series": metadata.get("Series"),
        "dvd_id": metadata.get("DVD ID"),
        "content_id": metadata.get("Content ID"),
        "release_date": metadata.get("Release Date"),
        "runtime": metadata.get("Runtime"),
        "studio": metadata.get("Studio"),
        "director": metadata.get("Director"),
        "genres": genres,
        "actresses": actresses,
        "preview_images": preview_urls,
        "poster": poster_url,
    }


def _add_text(parent, name: str, value: Optional[str]):
    if value:
        child = SubElement(parent, name)
        child.text = value


def _build_nfo(metadata, title, genres, actresses, poster_url, local_fanart) -> str:
    movie = Element("movie")
    release_date = metadata.get("Release Date")
    year = release_date[:4] if release_date and release_date[:4].isdigit() else None

    for tag_name in ("title", "originaltitle", "sorttitle", "localtitle"):
        _add_text(movie, tag_name, title)

    _add_text(movie, "year", year)
    _add_text(movie, "releasedate", release_date)

    runtime = re.search(r"\d+", metadata.get("Runtime") or "")
    _add_text(movie, "runtime", runtime.group(0) if runtime else None)
    _add_text(movie, "plot", metadata.get("Plot"))
    _add_text(movie, "studio", metadata.get("Studio"))
    _add_text(movie, "director", metadata.get("Director"))
    _add_text(movie, "set", metadata.get("Series"))

    for genre in genres:
        _add_text(movie, "genre", genre)

    for actress in actresses:
        actor = SubElement(movie, "actor")
        _add_text(actor, "name", actress)

    for id_type, value in (
        ("dvdid", metadata.get("DVD ID")),
        ("contentid", metadata.get("Content ID")),
    ):
        if value:
            unique_id = SubElement(movie, "uniqueid", {"type": id_type})
            unique_id.text = value

    _add_text(movie, "thumb", poster_url)

    if local_fanart:
        fanart = SubElement(movie, "fanart")
        for filename in local_fanart:
            _add_text(fanart, "thumb", filename)

    raw_xml = tostring(movie, encoding="utf-8")
    return (
        minidom.parseString(raw_xml)
        .toprettyxml(indent="  ", encoding="utf-8")
        .decode("utf-8")
    )


def _write_text(path, contents: str, label: str):
    try:
        Path(path).write_text(contents, encoding="utf-8")
        print(f"{label} written -> {path}", file=sys.stderr)
    except OSError as error:
        print(f"Failed saving {label}: {error}", file=sys.stderr)


def _select_movie(query: Optional[str], direct_link: Optional[str]):
    if direct_link:
        return {"link": direct_link, "title": None}

    query = query or input("Enter your search query: ").strip()
    results = fetch_search(query)

    if not results:
        return None
    if len(results) == 1:
        return results[0]

    for number, result in enumerate(results, start=1):
        print(f"{number}) {result['code']} — {result['title']}")

    while True:
        choice = input("Choose number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Search JAVDatabase and export metadata as Kodi NFO or JSON.",
        epilog=(
            "Examples:\n"
            "  javdb\n"
            "  javdb -q SONE-763 -o movie.nfo\n"
            "  javdb -q SONE-763 --json -o metadata.json\n"
            "  javdb -l https://www.javdatabase.com/movies/sone-763/ -d"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-q", "--query", help="Movie ID or search text")
    parser.add_argument("-l", "--link", help="Direct JAVDatabase movie URL")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument(
        "-d", "--download", action="store_true", help="Download poster and previews"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of NFO"
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    try:
        selected = _select_movie(args.query, args.link)
    except Exception as error:
        print(f"Search failed: {error}", file=sys.stderr)
        return 1

    if not selected:
        print("No results.")
        return 0

    # One request supplies every parser instead of downloading the page three times.
    metadata, poster_url, previews = fetch_movie_details(selected["link"])
    title = metadata.get("Title") or selected.get("title") or "movie"
    genres = _split_values(metadata.get("Genre(s)"), ("genre", "genres", "genre(s)"))
    actresses = _split_values(metadata.get("Idol(s)/Actress(es)"))

    folder = None
    default_nfo_base = None
    local_fanart = []

    if args.download:
        folder, default_nfo_base = _prepare_download_folder(metadata, title)
        local_fanart = _download_artwork(folder, poster_url, previews)

    if args.json:
        document = json.dumps(
            _build_json(selected, metadata, genres, actresses, previews, poster_url),
            ensure_ascii=False,
            indent=2,
        )
        print(document)

        output = args.output or (folder / "metadata.json" if folder else None)
        if output:
            _write_text(output, document, "JSON")
        return 0

    document = _build_nfo(metadata, title, genres, actresses, poster_url, local_fanart)
    print(document)

    output = args.output or (folder / f"{default_nfo_base}.nfo" if folder else None)
    if output:
        _write_text(output, document, "NFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
