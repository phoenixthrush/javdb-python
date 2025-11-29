import argparse
import json
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
        # Extract DVD code
        code_a = card.select_one("p.pcard a, p.display-6.pcard a")
        code = code_a.get_text(strip=True) if code_a else None
        link = code_a["href"] if code_a and code_a.has_attr("href") else None

        # Extract title
        desc_a = card.select_one(".mt-auto a")
        title = desc_a.get_text(strip=True) if desc_a else None

        # Extract release date (YYYY-MM-DD format)
        release_date = None
        mt_auto = card.select_one(".mt-auto")

        if mt_auto:
            text = mt_auto.get_text(" ", strip=True)
            match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if match:
                release_date = match.group(1)

        # Extract studio name
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
    """Convert string to filesystem-safe name."""
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)  # Replace invalid chars with dash
    name = re.sub(r"\s+", "_", name)  # Replace whitespace with underscore
    return name[:200]  # Limit length


def main(query_arg=None, link_arg=None, output_path=None, download=False):
    # If link is provided directly, skip the search
    if link_arg:
        selected = {"link": link_arg, "title": None}
    else:
        if query_arg:
            query = str(query_arg).strip()
        else:
            query = input("Enter your search query (e.g. SONE-763): ").strip()
        if not query:
            print("Empty query provided.")
            return

        try:
            items = fetch_search(query)
        except Exception as e:
            print("Error fetching search:", e)
            return

        if not items:
            print("No results found.")
            return

        # If multiple results, ask the user which one to select
        def choose_item(items):
            if len(items) == 1:
                return 0
            print(f"Found {len(items)} results:")
            for i, it in enumerate(items, start=1):
                code = it.get("code") or "N/A"
                title = it.get("title") or ""
                short = (title[:100] + "...") if len(title) > 100 else title
                print(f"{i}) {code} — {short}")
            while True:
                choice = input(
                    f"Enter the number of the item to select (1-{len(items)}) or 0 to cancel: "
                ).strip()

                if not choice:
                    continue
                if choice == "0":
                    return None
                if choice.isdigit():
                    idx = int(choice)
                    if 1 <= idx <= len(items):
                        return idx - 1

                print("Invalid choice, please try again.")

        # Ask the user to choose an item (interactive)
        sel_idx = choose_item(items)

        if sel_idx is None:
            print("Selection cancelled.")
            return

        selected = items[sel_idx]

    # Extract preview image URLs from movie page
    def fetch_preview_images(page_url):
        """Fetch preview image URLs from gallery."""
        try:
            r = requests.get(page_url, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print("Error fetching movie page:", e)
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
                    "img": img_tag["src"]
                    if img_tag and img_tag.has_attr("src")
                    else None,
                }
            )
        return images

    if not selected.get("link"):
        print("No link available to fetch images.")
        return

    # Extract all movie metadata from page
    def fetch_movie_metadata(page_url):
        """Fetch metadata: title, series, IDs, dates, genres, actresses, etc."""
        try:
            r = requests.get(page_url, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print("Error fetching movie page for metadata:", e)
            return {}

        s = BeautifulSoup(r.text, "html.parser")
        meta = {}

        # Try multiple title sources: h1 > og:title > fallback
        title_el = s.select_one("h1.entry-title, h1.post-title, h1")
        if title_el and title_el.get_text(strip=True):
            meta["Title"] = title_el.get_text(strip=True)
        else:
            og = s.select_one('meta[property="og:title"][content]')
            meta["Title"] = (
                og["content"]
                if og and og.has_attr("content")
                else selected.get("title")
            )

        # Use page text with separator for regex patterns
        page_text = s.get_text("||", strip=True)

        def labeled_value(label):
            """Extract single value from labeled HTML element."""
            for p in s.find_all(["p", "div", "li"]):
                b = p.find("b")
                if b and label.lower() in b.get_text(strip=True).lower():
                    # Look for anchor after label
                    a = p.find("a")

                    if a and a.get_text(strip=True):
                        return a.get_text(strip=True)

                    # Fallback: get text after <b> tag
                    current = b.next_sibling

                    while current:
                        if isinstance(current, str):
                            val = current.strip(" :\n\t")
                            if val:
                                return val
                        else:
                            text = current.get_text(strip=True)
                            if text:
                                return text
                        current = current.next_sibling
            return None

        def labeled_values(label):
            """Extract multiple values from labeled HTML element."""
            values = []
            for p in s.find_all(["p", "div", "li"]):
                b = p.find("b")

                if b and label.lower() in b.get_text(strip=True).lower():
                    # Collect all anchors within element
                    for a in p.find_all("a"):
                        txt = a.get_text(strip=True)
                        if txt:
                            values.append(txt)
            return values

        def extract_label(label_patterns):
            """Extract value using regex patterns on page text."""
            for pat in label_patterns:
                regex = re.compile(
                    rf"{pat}\s*[:\-–]?\s*(.*?)\s*(?:\|\||$)", re.I | re.S
                )

                m = regex.search(page_text)

                if m:
                    val = m.group(1).strip()
                    val = re.sub(r"\s{2,}", " ", val)  # Collapse whitespace
                    return val
            return None

        meta["JAV Series"] = labeled_value("JAV Series") or extract_label(
            [r"JAV Series", r"Series"]
        )
        meta["DVD ID"] = labeled_value("DVD ID") or extract_label(
            [r"DVD ID", r"DVD", r"DVD-ID"]
        )
        meta["Content ID"] = labeled_value("Content ID") or extract_label(
            [r"Content ID", r"Content-ID", r"Content"]
        )
        meta["Release Date"] = labeled_value("Release Date") or extract_label(
            [r"Release Date", r"Released", r"Date"]
        )
        meta["Runtime"] = labeled_value("Runtime") or extract_label(
            [r"Runtime", r"Running Time", r"Length"]
        )

        # Prefer explicit labeled HTML for Studio
        studio = labeled_value("Studio")
        if not studio:
            studio = extract_label([r"Studio", r"Label"])
        meta["Studio"] = studio
        meta["Director"] = labeled_value("Director") or extract_label(
            [r"Director", r"Directed by"]
        )

        # Extract genres
        genres = set()
        labeled_genres = labeled_values("Genre(s)")
        for g in labeled_genres:
            if g:
                genres.add(g)

        # Try generic selectors if no labeled genres found
        if not genres:
            for a in s.select('a[rel="tag"], .genres a, .post-categories a, .tags a'):
                txt = a.get_text(strip=True)
                if txt:
                    genres.add(txt)

        # Fallback to regex extraction
        if not genres:
            g = extract_label([r"Genre\(s\)", r"Genres", r"Genre"])
            if g:
                for part in re.split(r"[,/|•;]+", g):
                    part = part.strip()
                    if part:
                        genres.add(part)

        meta["Genre(s)"] = ", ".join(sorted(genres)) if genres else None

        # Extract actresses
        actresses = set()
        labeled_actresses = labeled_values("Idol(s)/Actress(es)")

        for a in labeled_actresses:
            if a:
                actresses.add(a)

        # Try generic link extraction if no labeled actresses found
        if not actresses:
            for a in s.select("a"):
                href = a.get("href", "")
                if (
                    "/actresses/" in href
                    or "/actors/" in href
                    or "/stars/" in href
                    or "/people/" in href
                ):
                    name = a.get_text(strip=True)
                    if name:
                        actresses.add(name)

        # Fallback to regex extraction
        if not actresses:
            a_label = extract_label(
                [r"Idol\(s\)/Actress\(es\)", r"Actress\(es\)", r"Idol\(s\)"]
            )

            if a_label:
                for part in re.split(r"[,/|•;]+", a_label):
                    part = part.strip()
                    if part:
                        actresses.add(part)

        meta["Idol(s)/Actress(es)"] = (
            ", ".join(sorted(actresses)) if actresses else None
        )

        # Normalize empty strings to None for metadata fields
        for key in [
            "JAV Series",
            "DVD ID",
            "Content ID",
            "Release Date",
            "Runtime",
            "Studio",
            "Director",
        ]:
            val = meta.get(key)

            if val is not None and isinstance(val, str) and not val.strip():
                meta[key] = None

        # Ensure Genre(s) is a cleaned string or None
        if (
            meta.get("Genre(s)")
            and isinstance(meta["Genre(s)"], str)
            and not meta["Genre(s)"].strip()
        ):
            meta["Genre(s)"] = None

        # Try to capture code-like IDs if not found via labeled extraction
        page_all_text = s.get_text(" ", strip=True)

        if not meta.get("DVD ID"):
            m = re.search(r"\b([A-Z]{2,}-?\d{2,}-?\d{1,})\b", page_all_text)
            if m:
                meta["DVD ID"] = m.group(1)

        if not meta.get("Content ID"):
            m2 = re.search(r"\b([a-z]{2,}\d{4,})\b", page_all_text, re.I)
            if m2:
                meta["Content ID"] = m2.group(1)

        return meta

    # Fetch metadata and images, output as JSON
    metadata = fetch_movie_metadata(selected.get("link")) or {}
    imgs = fetch_preview_images(selected.get("link")) or []

    def to_list(s):
        """Convert string to list, split by common separators."""
        if not s:
            return []

        if isinstance(s, (list, tuple)):
            return list(s)

        parts = [p.strip() for p in re.split(r"[,/|•;]+", s) if p.strip()]

        return parts

    data = {
        "link": selected.get("link"),
        "title": metadata.get("Title") or selected.get("title"),
        "jav_series": metadata.get("JAV Series"),
        "dvd_id": metadata.get("DVD ID"),
        "content_id": metadata.get("Content ID"),
        "release_date": metadata.get("Release Date"),
        "runtime": metadata.get("Runtime"),
        "studio": metadata.get("Studio"),
        "director": metadata.get("Director"),
        "genres": to_list(metadata.get("Genre(s)")),
        "actresses": to_list(metadata.get("Idol(s)/Actress(es)")),
        "preview_images": [
            im.get("full") or im.get("preview")
            for im in imgs
            if (im.get("full") or im.get("preview"))
        ],
    }

    pretty = json.dumps(data, indent=4, ensure_ascii=False)
    print(pretty)

    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(pretty)

            print(f"Wrote metadata to {output_path}")
        except Exception as e:
            print("Error writing output file:", e)

    if download and data.get("preview_images"):
        # Prepare download directory
        folder_name = data.get("dvd_id") or data.get("title") or query
        folder = safe_filename(folder_name)
        outdir = f"{folder}/preview"

        os.makedirs(outdir, exist_ok=True)

        # Save metadata JSON copy to folder
        json_filename = (
            f"{data.get('content_id') or data.get('dvd_id') or 'metadata'}.json"
        )

        json_path = os.path.join(folder, json_filename)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(pretty)

            print(f"Stored metadata to {json_path}")
        except Exception as e:
            print(f"Failed to store metadata: {e}")

        # Download all preview images
        print(f"Downloading {len(data['preview_images'])} images to {outdir}")
        for url in data["preview_images"]:
            try:
                parsed = urlparse(url)
                name = unquote(parsed.path.rsplit("/", 1)[-1])

                if not name:
                    name = f"img_{abs(hash(url))}.jpg"

                path = os.path.join(outdir, name)
                resp = requests.get(url, stream=True, timeout=30)
                resp.raise_for_status()

                with open(path, "wb") as fd:
                    for chunk in resp.iter_content(1024 * 8):
                        if chunk:
                            fd.write(chunk)

                print(f"Downloaded {name}")
            except Exception as e:
                print(f"Failed to download {url}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search javdatabase and extract metadata/images"
    )

    parser.add_argument("-q", "--query", help="Search query (e.g. SONE-763)")
    parser.add_argument("-l", "--link", help="Direct link to movie page (skips search)")
    parser.add_argument("-o", "--output", help="Write JSON output to file")
    parser.add_argument(
        "-d",
        "--download",
        action="store_true",
        help="Download preview images to downloads/<dvd_id>/",
    )

    args = parser.parse_args()

    main(
        args.query, link_arg=args.link, output_path=args.output, download=args.download
    )
