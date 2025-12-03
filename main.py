import argparse
import os
import re
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup
from requests.utils import requote_uri


def fetch_search(query):
    """Search javdatabase.com and extract result cards."""
    search_url = f"https://www.javdatabase.com/?post_type=movies%2Cuncensored&s={requote_uri(query)}"
    resp = requests.get(search_url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cards = soup.select(".card.borderlesscard, .card.h-100.borderlesscard")
    results = []

    for card in cards:
        code_a = card.select_one("p.pcard a, p.display-6.pcard a")
        code = code_a.get_text(strip=True) if code_a else None
        link = code_a["href"] if code_a and code_a.has_attr("href") else None

        desc_a = card.select_one(".mt-auto a")
        title = desc_a.get_text(strip=True) if desc_a else None

        release_date = None
        mt_auto = card.select_one(".mt-auto")
        if mt_auto:
            text = mt_auto.get_text(" ", strip=True)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if m:
                release_date = m.group(1)

        studio_a = card.select_one("span.btn a, span.btn-primary a")
        studio = studio_a.get_text(strip=True) if studio_a else None

        results.append(
            {
                "code": code,
                "title": title,
                "link": link,
                "date": release_date,
                "studio": studio,
            }
        )

    return results


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", "_", name)
    return name[:200]


def fetch_preview_images(page_url):
    """Fetch preview image URLs from gallery."""
    try:
        r = requests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return []

    s = BeautifulSoup(r.text, "html.parser")
    gallery = s.select_one("div.row.g-3") or s.select_one(".row.g-3")
    anchors = (
        gallery.select("a[data-image-src]")
        if gallery
        else s.select("a[data-image-src]")
    )

    images = []
    for a in anchors:
        img_tag = a.find("img")
        images.append(
            {
                "preview": a.get("data-image-src"),
                "full": a.get("data-image-href"),
                "img": img_tag["src"] if img_tag and img_tag.has_attr("src") else None,
            }
        )

    return images


def fetch_poster_url(page_url):
    """Extract poster from div#poster-container."""
    try:
        r = requests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return None

    s = BeautifulSoup(r.text, "html.parser")
    poster_div = s.find("div", {"id": "poster-container"})
    if poster_div:
        img = poster_div.find("img")
        if img and img.has_attr("src"):
            return img["src"]

    img = s.select_one("div.poster img, .poster img, img[alt$='JAV Movie Cover']")
    if img and img.has_attr("src"):
        return img["src"]

    return None


def fetch_movie_metadata(page_url):
    """Scrape title, IDs, dates, genres, actresses, etc."""
    try:
        r = requests.get(page_url, timeout=15)
        r.raise_for_status()
    except Exception:
        return {}

    s = BeautifulSoup(r.text, "html.parser")
    meta = {}

    # title
    t = s.select_one("h1.entry-title, h1.post-title, h1")
    meta["Title"] = t.get_text(strip=True) if t else None

    page_text = s.get_text("||", strip=True)

    def labeled_value(label):
        for p in s.find_all(["p", "div", "li"]):
            b = p.find("b")
            if b and label.lower() in b.get_text(strip=True).lower():
                a = p.find("a")
                if a:
                    val = a.get_text(strip=True)
                    if val:
                        return val
                cur = b.next_sibling
                while cur:
                    if isinstance(cur, str):
                        v = cur.strip(" :\n\t")
                        if v:
                            return v
                    else:
                        txt = cur.get_text(strip=True)
                        if txt:
                            return txt
                    cur = cur.next_sibling
        return None

    def labeled_values(label):
        vals = []
        for p in s.find_all(["p", "div", "li"]):
            b = p.find("b")
            if b and label.lower() in b.get_text(strip=True).lower():
                for a in p.find_all("a"):
                    txt = a.get_text(strip=True)
                    if txt:
                        vals.append(txt)
        return vals

    def extract(patterns):
        for pat in patterns:
            regex = re.compile(rf"{pat}\s*[:\-–]?\s*(.*?)\s*(?:\|\||$)", re.I | re.S)
            m = regex.search(page_text)
            if m:
                value = m.group(1).strip()
                return re.sub(r"\s{2,}", " ", value)
        return None

    meta["DVD ID"] = labeled_value("DVD ID") or extract(["DVD ID", "DVD"])
    meta["Content ID"] = labeled_value("Content ID") or extract(["Content ID"])
    meta["Release Date"] = labeled_value("Release Date") or extract(["Released"])
    meta["Runtime"] = labeled_value("Runtime") or extract(["Runtime"])
    meta["Studio"] = labeled_value("Studio") or extract(["Studio"])
    meta["Director"] = labeled_value("Director") or extract(["Director"])

    genres = set(labeled_values("Genre"))
    meta["Genre(s)"] = ", ".join(sorted(genres)) if genres else None

    actresses = set(labeled_values("Idol"))
    meta["Idol(s)/Actress(es)"] = ", ".join(sorted(actresses)) if actresses else None

    return meta


def main(query_arg=None, link_arg=None, output_path=None, download=False):
    # Search or direct link
    if link_arg:
        selected = {"link": link_arg, "title": None}
    else:
        query = query_arg or input("Enter your search query: ").strip()
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

    # Prepare XML
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

    # Genres
    if metadata.get("Genre(s)"):
        for g in re.split(r"[,|/;]+", metadata["Genre(s)"]):
            g = g.strip()
            if g:
                tag(movie, "genre", g)

    # Actors
    if metadata.get("Idol(s)/Actress(es)"):
        for act in re.split(r"[,|/;]+", metadata["Idol(s)/Actress(es)"]):
            act = act.strip()
            if act:
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

    if download:
        folder_name = metadata.get("DVD ID") or title or "movie"
        folder = safe_filename(folder_name)
        os.makedirs(folder, exist_ok=True)

        preview_folder = os.path.join(folder, "preview")
        os.makedirs(preview_folder, exist_ok=True)

        open(os.path.join(folder, "preview", ".ignore"), "a").close()

        # ---- Download poster → MOVIE_FOLDER ----
        if postersrc:
            parsed = urlparse(postersrc)
            poster_filename = unquote(parsed.path.split("/")[-1])
            # poster_filename = f"thumb.{parsed.path.split('.')[-1]}"
            poster_path = os.path.join(folder, "preview", poster_filename)

            try:
                r = requests.get(postersrc, stream=True, timeout=20)
                r.raise_for_status()
                with open(os.path.join(poster_path), "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                print(f"Poster downloaded → {poster_path}")
            except Exception as e:
                print("Poster download failed:", e)
                poster_filename = None

        # ---- Download previews → MOVIE_FOLDER/preview ----
        for img in previews:
            url = img.get("full") or img.get("preview")
            if not url:
                continue
            parsed = urlparse(url)
            fname = unquote(parsed.path.split("/")[-1])
            dest = os.path.join(preview_folder, fname)
            try:
                r = requests.get(url, stream=True, timeout=20)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                local_fanarts.append(f"preview/{fname}")
                print("Downloaded preview →", dest)
            except Exception as e:
                print("Preview download failed:", e)

        # If --download is enabled, ALWAYS write NFO inside movie folder
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
    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_str)
            print("NFO written →", output_path)
        except Exception as e:
            print("Failed saving NFO:", e)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Search javdatabase & export NFO")
    p.add_argument("-q", "--query")
    p.add_argument("-l", "--link")
    p.add_argument("-o", "--output")
    p.add_argument("-d", "--download", action="store_true")
    args = p.parse_args()

    main(args.query, args.link, args.output, args.download)
