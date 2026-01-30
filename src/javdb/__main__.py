import argparse
import json
import os
import re
import sys
from html import unescape
from urllib.parse import unquote, urlparse

import niquests
from niquests.utils import requote_uri


def _clean_html_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _html_to_text(html: str) -> str:
    # Rough HTML -> plain text conversion with block separators
    html = re.sub(r"<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = unescape(html)

    # Normalize whitespace while preserving newlines
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in html.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _labeled_links(html: str, label: str):
    pattern = re.compile(
        rf"(?is)<(?:p|div|li)[^>]*>[^<]*<b[^>]*>[^<]*{re.escape(label)}[^<]*</b>(.*?)</(?:p|div|li)>"
    )
    vals = []
    for m in pattern.finditer(html):
        block = m.group(1)
        for a in re.findall(r"<a[^>]*>(.*?)</a>", block, flags=re.I | re.S):
            txt = _clean_html_text(a)
            if txt:
                vals.append(txt)
    return vals


def _labeled_single(html: str, label: str):
    # Capture the full contents of the container after the label, then clean it.
    pattern = re.compile(
        rf"(?is)<(?:p|div|li)[^>]*>\s*<b[^>]*>[^<]*{re.escape(label)}[^<]*</b>\s*[:\-–]?\s*(.*?)</(?:p|div|li)>"
    )
    m = pattern.search(html)
    if not m:
        return None

    block = m.group(1)
    # If there is another bold label in the same block, cut before it to avoid
    # swallowing following fields (e.g. Genre(s) ending up as Director).
    block = re.split(r"<b[^>]*>.*?</b>", block, 1)[0]
    block = re.sub(r"(?is)<br\s*/?>", "\n", block)
    first_line = next((ln for ln in block.splitlines() if ln.strip()), "")
    return _clean_html_text(first_line)


def fetch_search(query):
    """Search javdatabase.com and extract result cards using regex only."""
    search_url = f"https://www.javdatabase.com/?post_type=movies%2Cuncensored&s={requote_uri(query)}"
    resp = niquests.get(search_url, timeout=15)
    resp.raise_for_status()
    html: str = resp.text or ""

    # Roughly isolate each result card block
    card_pattern = re.compile(
        r"(?is)<div[^>]+class=\"[^\"]*\bcard\b[^\"]*\bborderlesscard\b[^\"]*\"[^>]*>(.*?)</div>"
    )
    results = []

    for m in card_pattern.finditer(html):
        block = m.group(1)

        code = None
        link = None
        # Code + link from p.pcard a / p.display-6.pcard a
        m_code = re.search(
            r"(?is)<p[^>]+class=\"[^\"]*\bpcard\b[^\"]*\"[^>]*>.*?<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>",
            block,
        )
        if m_code:
            link = m_code.group(1)
            code = _clean_html_text(m_code.group(2))

        # Title from .mt-auto a, fallback to code text
        title = None
        m_title_block = re.search(
            r"(?is)<(?:div|p|span)[^>]+class=\"[^\"]*\bmt-auto\b[^\"]*\"[^>]*>(.*?)</(?:div|p|span)>",
            block,
        )
        if m_title_block:
            inner = m_title_block.group(1)
            m_title = re.search(r"(?is)<a[^>]*>(.*?)</a>", inner)
            if m_title:
                title = _clean_html_text(m_title.group(1))
        if not title:
            title = code

        # Release date as first YYYY-MM-DD in the card text
        text = _clean_html_text(block)
        release_date = None
        m_date = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if m_date:
            release_date = m_date.group(1)

        # Studio from span.btn a / span.btn-primary a
        studio = None
        m_stu = re.search(
            r"(?is)<span[^>]+class=\"[^\"]*\bbtn(?:-primary)?\b[^\"]*\"[^>]*>.*?<a[^>]*>(.*?)</a>",
            block,
        )
        if m_stu:
            studio = _clean_html_text(m_stu.group(1))

        results.append(
            {
                "code": code,
                "title": title,
                "link": link,
                "date": release_date,
                "studio": studio,
            }
        )

    return [r for r in results if r["link"]]


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", "_", name)
    return name[:200]


def fetch_preview_images(page_url):
    """Fetch preview image URLs from gallery."""
    try:
        r = niquests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return []

    html: str = r.text or ""
    images = []

    # Match anchors carrying data-image-src / data-image-href
    anchor_pattern = re.compile(
        r"(?is)<a([^>]*data-image-src=\"[^\"]+\"[^>]*)>(.*?)</a>"
    )

    for attrs, inner in anchor_pattern.findall(html):
        preview = None
        full = None
        img_src = None

        m_prev = re.search(r"data-image-src=\"([^\"]+)\"", attrs, flags=re.I)
        if m_prev:
            preview = m_prev.group(1)

        m_full = re.search(r"data-image-href=\"([^\"]+)\"", attrs, flags=re.I)
        if m_full:
            full = m_full.group(1)

        m_img = re.search(r"<img[^>]+src=\"([^\"]+)\"", inner, flags=re.I)
        if m_img:
            img_src = m_img.group(1)

        images.append({"preview": preview, "full": full, "img": img_src})

    return [img for img in images if img["preview"] or img["full"]]


def fetch_poster_url(page_url):
    """Extract poster from div#poster-container."""
    try:
        r = niquests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return None

    html: str = r.text or ""

    # Try div#poster-container first
    m = re.search(r"(?is)<div[^>]+id=\"poster-container\"[^>]*>(.*?)</div>", html)
    if m:
        block = m.group(1)
        m_img = re.search(r"<img[^>]+src=\"([^\"]+)\"", block, flags=re.I)
        if m_img:
            return m_img.group(1)

    # Fallback: .poster img
    m_img = re.search(
        r"(?is)<div[^>]+class=\"[^\"]*\bposter\b[^\"]*\"[^>]*>.*?<img[^>]+src=\"([^\"]+)\"",
        html,
    )
    if m_img:
        return m_img.group(1)

    # Fallback: img with alt ending in 'JAV Movie Cover'
    m_img = re.search(
        r"<img[^>]+alt=\"[^\"]*JAV Movie Cover[^\"]*\"[^>]*src=\"([^\"]+)\"",
        html,
        flags=re.I,
    )
    if m_img:
        return m_img.group(1)

    return None


def fetch_movie_metadata(page_url):
    """Scrape title, IDs, dates, genres, actresses, etc."""
    try:
        r = niquests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return {}

    html: str = r.text or ""
    meta = {}

    # Title from first h1
    m_title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html)
    meta["Title"] = _clean_html_text(m_title.group(1)) if m_title else None

    page_text = _html_to_text(html)

    def extract(patterns, *, max_words: int | None = None):
        for pat in patterns:
            regex = re.compile(rf"{pat}\s*[:\-–]?\s*(.*?)\s*(?:\n|$)", re.I)
            m = regex.search(page_text)
            if m:
                value = m.group(1).strip()
                value = re.sub(r"\s{2,}", " ", value)
                if max_words is not None:
                    words = value.split()
                    if len(words) > max_words:
                        value = " ".join(words[:max_words])
                return value
        return None

    meta["DVD ID"] = _labeled_single(html, "DVD ID") or extract(
        ["DVD ID", "DVD"], max_words=4
    )
    meta["Content ID"] = _labeled_single(html, "Content ID") or extract(
        ["Content ID"], max_words=4
    )
    meta["Release Date"] = _labeled_single(html, "Release Date") or extract(
        ["Released"],
        max_words=4,
    )
    meta["Runtime"] = _labeled_single(html, "Runtime") or extract(
        ["Runtime"], max_words=8
    )
    meta["Studio"] = _labeled_single(html, "Studio") or extract(["Studio"], max_words=8)
    director = _labeled_single(html, "Director")
    if director is None:
        director = extract(["Director"], max_words=8)
    elif not director:
        director = None
    meta["Director"] = director
    meta["Series"] = _labeled_single(html, "Series") or extract(["Series"], max_words=8)

    genres = set(_labeled_links(html, "Genre"))
    meta["Genre(s)"] = ", ".join(sorted(genres)) if genres else None

    actresses = set(_labeled_links(html, "Idol"))
    meta["Idol(s)/Actress(es)"] = ", ".join(sorted(actresses)) if actresses else None

    return meta


def main():
    p = argparse.ArgumentParser(
        description="Search javdatabase.com and export metadata as Kodi NFO or JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  javdb                      # interactive search + NFO to stdout\n"
            "  javdb -q SONE-763          # non-interactive search by ID\n"
            "  javdb -q SONE-763 -o out.nfo\n"
            "  javdb -q SONE-763 --json -o metadata.json\n"
            "  javdb --link https://www.javdatabase.com/movies/sone-763/ --download\n"
        ),
    )
    p.add_argument(
        "-q",
        "--query",
        help="Search query (e.g. movie ID like SONE-763 or a text phrase)",
    )
    p.add_argument(
        "-l",
        "--link",
        help="Direct movie page URL on javdatabase.com (skips search)",
    )
    p.add_argument(
        "-o",
        "--output",
        help="Output file path (NFO/XML by default, or JSON when --json is used)",
    )
    p.add_argument(
        "-d",
        "--download",
        action="store_true",
        help="Download poster + previews into a folder named after the DVD ID",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output metadata as JSON instead of NFO/XML",
    )

    args = p.parse_args()
    output_path = args.output

    # Search or direct link
    if args.link:
        selected = {"link": args.link, "title": None}
    else:
        query = args.query or input("Enter your search query: ").strip()
        items = fetch_search(query)
        if not items:
            print("No results.")
            return

        if len(items) == 1:
            selected = items[0]
        else:
            for i, it in enumerate(items, 1):
                print(f"{i}) {it['code']} — {it['title']}")
            while True:
                c = input("Choose number: ").strip()
                if c.isdigit() and 1 <= int(c) <= len(items):
                    selected = items[int(c) - 1]
                    break

    # Collect metadata and images
    metadata = fetch_movie_metadata(selected["link"])
    postersrc = fetch_poster_url(selected["link"])
    previews = fetch_preview_images(selected["link"])

    # Normalize lists used by both JSON and NFO
    genres_list = []
    if metadata.get("Genre(s)"):
        for g in re.split(r"[,|/;]+", metadata["Genre(s)"]):
            g = g.strip()
            if not g:
                continue
            if re.fullmatch(r"genres?|genre\(s\)?", g, flags=re.I):
                continue
            genres_list.append(g)

    actresses_list = []
    if metadata.get("Idol(s)/Actress(es)"):
        for act in re.split(r"[,|/;]+", metadata["Idol(s)/Actress(es)"]):
            act = act.strip()
            if act:
                actresses_list.append(act)

    # JSON representation
    json_obj = {
        "link": selected["link"],
        "title": metadata.get("Title") or selected.get("title"),
        "jav_series": metadata.get("Series"),
        "dvd_id": metadata.get("DVD ID"),
        "content_id": metadata.get("Content ID"),
        "release_date": metadata.get("Release Date"),
        "runtime": metadata.get("Runtime"),
        "studio": metadata.get("Studio"),
        "director": metadata.get("Director"),
        "genres": genres_list,
        "actresses": actresses_list,
        "preview_images": [
            img.get("full") or img.get("preview")
            for img in previews
            if img.get("full") or img.get("preview")
        ],
        "poster": postersrc,
    }

    # If JSON is requested, output JSON and skip NFO/XML
    if args.json:
        json_str = json.dumps(json_obj, ensure_ascii=False, indent=2)
        print(json_str)

        if args.download:
            # With --download and JSON, default to metadata.json in the movie folder
            folder_name = metadata.get("DVD ID") or json_obj["title"] or "movie"
            folder = safe_filename(folder_name)
            os.makedirs(folder, exist_ok=True)
            if not output_path:
                output_path = os.path.join(folder, "metadata.json")

        if output_path:
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print("JSON written ->", output_path, file=sys.stderr)
            except Exception as e:
                print("Failed saving JSON:", e, file=sys.stderr)

        return

    # Prepare XML (NFO)
    from xml.etree.ElementTree import Element, SubElement, tostring
    import xml.dom.minidom as md

    def tag(parent, name, val):
        if val:
            e = SubElement(parent, name)
            e.text = val
            return e

    movie = Element("movie")

    title = metadata.get("Title") or selected.get("title")
    release_date = metadata.get("Release Date")
    year = (
        release_date[:4] if release_date and re.match(r"\d{4}", release_date) else None
    )

    tag(movie, "title", title)
    tag(movie, "originaltitle", title)
    tag(movie, "sorttitle", title)
    tag(movie, "localtitle", title)
    tag(movie, "year", year)
    tag(movie, "releasedate", release_date)

    runtime = None
    if metadata.get("Runtime"):
        m = re.search(r"(\d+)", metadata["Runtime"])
        runtime = m.group(1) if m else None
    tag(movie, "runtime", runtime)

    tag(movie, "plot", "")
    tag(movie, "review", "")
    tag(movie, "biography", "")

    # Studios
    if metadata.get("Studio"):
        tag(movie, "studio", metadata["Studio"])

    tag(movie, "director", metadata.get("Director"))

    # Series (optional)
    if metadata.get("Series"):
        tag(movie, "set", metadata["Series"])

    # Genres
    for g in genres_list:
        tag(movie, "genre", g)

    # Actors
    for act in actresses_list:
        ae = SubElement(movie, "actor")
        tag(ae, "name", act)
        tag(ae, "role", "")

    # Unique IDs
    if metadata.get("DVD ID"):
        u = SubElement(movie, "uniqueid")
        u.set("type", "dvdid")
        u.text = metadata["DVD ID"]

    if metadata.get("Content ID"):
        u = SubElement(movie, "uniqueid")
        u.set("type", "contentid")
        u.text = metadata["Content ID"]

    # ----------------------------------------------------------------------
    # DOWNLOAD SECTION (local images + relative paths)
    # ----------------------------------------------------------------------
    poster_filename = None
    local_fanarts = []

    if args.download:
        folder_name = metadata.get("DVD ID") or title or "movie"
        folder = safe_filename(folder_name)
        os.makedirs(folder, exist_ok=True)

        preview_folder = os.path.join(folder, "preview")
        os.makedirs(preview_folder, exist_ok=True)

        open(os.path.join(folder, "preview", ".ignore"), "a").close()

        # ---- Download poster -> MOVIE_FOLDER ----
        if postersrc:
            parsed = urlparse(postersrc)
            poster_filename = unquote(parsed.path.split("/")[-1])
            poster_path = os.path.join(folder, "preview", poster_filename)

            try:
                r = niquests.get(postersrc, stream=True, timeout=20)
                r.raise_for_status()
                with open(os.path.join(poster_path), "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                print(f"Poster downloaded -> {poster_path}")
            except Exception as e:
                print("Poster download failed:", e)
                poster_filename = None

        # ---- Download previews -> MOVIE_FOLDER/preview ----
        for img in previews:
            url = img.get("full") or img.get("preview")
            if not url:
                continue
            parsed = urlparse(url)
            fname = unquote(parsed.path.split("/")[-1])
            dest = os.path.join(preview_folder, fname)
            try:
                r = niquests.get(url, stream=True, timeout=20)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                local_fanarts.append(f"preview/{fname}")
                print("Downloaded preview ->", dest)
            except Exception as e:
                print("Preview download failed:", e)

        # If --download is enabled, ALWAYS write NFO inside movie folder by default
        output_path = output_path or os.path.join(
            folder, f"{os.listdir(folder)[0].rsplit('.', 1)[0]}.nfo"
        )

    # ----------------------------------------------------------------------
    # Artwork references in NFO (using relative paths)
    # ----------------------------------------------------------------------
    if poster_filename:
        tag(movie, "thumb", postersrc)

    if local_fanarts:
        fan = SubElement(movie, "fanart")
        for f in local_fanarts:
            fe = SubElement(fan, "thumb")
            fe.text = f

    # ----------------------------------------------------------------------
    # Final XML
    # ----------------------------------------------------------------------
    xml_str = (
        md.parseString(tostring(movie, encoding="utf-8"))
        .toprettyxml(indent="  ", encoding="utf-8")
        .decode("utf-8")
    )

    print(xml_str)

    # Save NFO (if output path defined or forced by download)
    if output_path := (output_path or args.output):
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_str)
            print("NFO written ->", output_path)
        except Exception as e:
            print("Failed saving NFO:", e)


if __name__ == "__main__":
    main()
