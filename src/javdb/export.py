"""JSON/NFO serialization and safe output files."""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".wmv",
    ".mov",
    ".flv",
    ".ts",
    ".webm",
    ".m4v",
}


def safe_filename(name):
    name = re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]+', "-", name.strip())
    name = re.sub(r"\s+", "_", name).strip("._-")[:120].rstrip(". ") or "movie"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return name


@contextmanager
def atomic_file(destination, overwrite=False):
    """Commit a completed sibling temporary file, refusing replacement by default."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and destination.exists():
        raise FileExistsError(
            f"{destination} already exists; use --overwrite to replace it"
        )
    descriptor, temporary = tempfile.mkstemp(
        prefix=".javdb-", dir=str(destination.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            yield output
        if overwrite:
            os.replace(temporary, str(destination))
        else:
            # A hard link commits without a race that could overwrite another file.
            os.link(temporary, str(destination))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_text(destination, document, overwrite=False):
    with atomic_file(destination, overwrite) as output:
        output.write((document.rstrip() + "\n").encode("utf-8"))


def to_json(value):
    """Serialize a movie or search results as readable UTF-8 JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def to_nfo(movie, poster=None, fanart=None):
    """Build Kodi movie XML, using remote artwork unless local paths are supplied."""
    root = Element("movie")

    def add(parent, tag, value, **attrs):
        if value:
            # XML 1.0 forbids control characters, even inside escaped text.
            value = re.sub(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(value)
            )
            SubElement(parent, tag, attrs).text = value

    add(root, "title", movie.get("title"))
    add(root, "originaltitle", movie.get("title"))
    release = movie.get("release_date")
    if release and re.match(r"\d{4}", release):
        add(root, "year", release[:4])
    add(root, "premiered", release)
    add(root, "releasedate", release)
    runtime = movie.get("runtime") or ""
    hours = re.search(r"(\d+)\s*(?:hours?|hrs?|h)\b", runtime, re.IGNORECASE)
    minutes = re.search(r"(\d+)\s*(?:minutes?|mins?|m)\b", runtime, re.IGNORECASE)
    if hours or minutes:
        duration = int(hours.group(1)) * 60 if hours else 0
        duration += int(minutes.group(1)) if minutes else 0
    else:
        number = re.search(r"\d+", runtime)
        duration = int(number.group()) if number else None
    add(root, "runtime", duration)
    for tag in ("plot", "studio", "director"):
        add(root, tag, movie.get(tag))
    if movie.get("series"):
        add(SubElement(root, "set"), "name", movie["series"])
    for genre in movie.get("genres", []):
        add(root, "genre", genre)
    for actress in movie.get("actresses", []):
        add(SubElement(root, "actor"), "name", actress)
    for key, kind in (("dvd_id", "dvdid"), ("content_id", "contentid")):
        add(
            root,
            "uniqueid",
            movie.get(key),
            type=kind,
            default="true" if key == "dvd_id" else "false",
        )
    add(root, "trailer", movie.get("trailer"))
    if movie.get("rating"):
        rating = movie["rating"]
        entry = SubElement(
            SubElement(root, "ratings"),
            "rating",
            {"name": "javdatabase", "max": str(rating["max"]), "default": "true"},
        )
        add(entry, "value", str(rating["value"]))
        add(entry, "votes", rating["votes"])
    add(
        root,
        "thumb",
        movie.get("poster") if poster is None else poster,
        aspect="poster",
    )
    images = movie.get("preview_images", []) if fanart is None else fanart
    if images:
        parent = SubElement(root, "fanart")
        for image in images:
            add(parent, "thumb", image)
    return (
        minidom.parseString(tostring(root, encoding="utf-8"))
        .toprettyxml(indent="  ", encoding="utf-8")
        .decode("utf-8")
        .rstrip()
    )


def movie_basename(folder, dvd_id, fallback):
    """Reuse a video stem only when exactly one video matches the DVD ID."""
    if dvd_id and Path(folder).is_dir():
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(dvd_id) + r"(?![A-Za-z0-9])", re.IGNORECASE
        )
        matches = [
            path
            for path in Path(folder).iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and pattern.search(path.stem)
        ]
        if len(matches) == 1:
            return matches[0].stem
    return safe_filename(fallback)
