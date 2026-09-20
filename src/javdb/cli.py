"""Command-line interface; stdout is reserved for data."""

import argparse
import math
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .client import Client, JavDBError
from .export import movie_basename, safe_filename, to_json, to_nfo, write_text


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def _retries(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description="Search JAVDatabase and export Kodi NFO or JSON metadata."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("-q", "--query", help="movie ID or search text")
    source.add_argument("-l", "--link", help="direct movie URL (HTTP or HTTPS)")
    parser.add_argument("-o", "--output", help="output file; '-' writes to stdout")
    parser.add_argument(
        "--json", action="store_true", help="export JSON instead of NFO"
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="list search results without fetching a movie",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--first", action="store_true", help="select the first search result"
    )
    selection.add_argument(
        "--select",
        type=_positive,
        metavar="N",
        help="select a result by its 1-based index",
    )
    parser.add_argument(
        "--page",
        type=_positive,
        default=1,
        metavar="N",
        help="first search page (default: 1)",
    )
    parser.add_argument(
        "--pages",
        type=_positive,
        default=1,
        metavar="N",
        help="maximum pages to search (default: 1)",
    )
    parser.add_argument(
        "-d",
        "--download",
        action="store_true",
        help="download poster and preview images",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        metavar="DIR",
        help="save metadata/artwork in this directory",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing metadata and artwork"
    )
    parser.add_argument(
        "--timeout",
        type=_timeout,
        default=15,
        metavar="SECONDS",
        help="socket timeout per attempt (default: 15)",
    )
    parser.add_argument(
        "--retries",
        type=_retries,
        default=2,
        metavar="N",
        help="retries after transient failures (default: 2)",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    return parser


def _prompt(message):
    print(message, end="", file=sys.stderr, flush=True)
    return input().strip()


def _listing(results):
    return "\n".join(
        "{}) {} — {}{}\n   {}".format(
            number,
            result["code"],
            result["title"],
            " ({})".format(result["date"]) if result["date"] else "",
            result["link"],
        )
        for number, result in enumerate(results, 1)
    )


def _select(results, args):
    if args.first or args.select:
        index = (args.select or 1) - 1
        if index >= len(results):
            raise ValueError(f"--select exceeds the {len(results)} available results")
        return results[index]
    exact = [
        result
        for result in results
        if result["code"].casefold() == args.query.strip().casefold()
    ]
    if len(exact) == 1:
        return exact[0]
    if len(results) == 1:
        return results[0]
    print(_listing(results), file=sys.stderr)
    if not sys.stdin.isatty():
        raise ValueError(
            "Multiple results; use --first or --select N for non-interactive selection"
        )
    while True:
        choice = _prompt("Choose a number (q to quit): ")
        if choice.casefold() == "q":
            raise EOFError
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]
        print(f"Enter a number from 1 to {len(results)}.", file=sys.stderr)


def _emit(document, output, overwrite):
    if output is None or str(output) == "-":
        print(document)
    else:
        write_text(output, document, overwrite)
        print(f"Saved {output}", file=sys.stderr)


def _artwork(client, movie, folder, reference_folder, overwrite):
    poster = movie.get("poster")
    previews = list(movie["preview_images"])
    failed = False
    items = [("poster", None, poster)] if poster else []
    items += [
        (f"preview-{index + 1:03d}", index, url) for index, url in enumerate(previews)
    ]
    for name, index, url in items:
        suffix = Path(urlsplit(url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            suffix = ".jpg"
        destination = folder / "artwork" / (name + suffix)
        try:
            if destination.is_file() and not overwrite:
                print(f"Keeping {destination}", file=sys.stderr)
            else:
                client.download(url, destination, overwrite=overwrite)
                print(f"Downloaded {destination}", file=sys.stderr)
            reference = Path(
                os.path.relpath(destination.resolve(), reference_folder.resolve())
            ).as_posix()
            if index is None:
                poster = reference
            else:
                previews[index] = reference
        except (JavDBError, OSError) as error:
            failed = True
            print(f"Artwork failed: {error}", file=sys.stderr)
    return poster, previews, failed


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.search and (
        args.link or args.download or args.directory or args.first or args.select
    ):
        parser.error(
            "--search cannot be combined with --link, --download, --directory, --first or --select"
        )
    if args.link and (args.first or args.select or args.page != 1 or args.pages != 1):
        parser.error("search selection and pagination cannot be used with --link")
    try:
        client = Client(timeout=args.timeout, retries=args.retries)
        if not args.link:
            if args.query is None:
                if not sys.stdin.isatty():
                    raise ValueError(
                        "Provide --query or --link when stdin is not a terminal"
                    )
                args.query = _prompt("Enter your search query: ")
            results = client.search(args.query, page=args.page, pages=args.pages)
            if args.search:
                _emit(
                    to_json(results) if args.json else _listing(results),
                    args.output,
                    args.overwrite,
                )
                return 0
            if not results:
                raise JavDBError("No results found")
            args.link = _select(results, args)["link"]
        movie = client.movie(args.link)
        name = safe_filename(movie["dvd_id"] or movie["title"])
        folder = args.directory
        if args.download and folder is None:
            folder = Path.cwd() / name
        output = args.output
        if output is None and folder is not None:
            basename = movie_basename(folder, movie["dvd_id"], name)
            output = folder / ("metadata.json" if args.json else basename + ".nfo")
        if (
            output is not None
            and str(output) != "-"
            and Path(output).exists()
            and not args.overwrite
        ):
            raise FileExistsError(
                f"{output} already exists; use --overwrite to replace it"
            )
        poster, previews, failed = None, None, False
        if args.download:
            reference_folder = (
                Path(output).parent
                if output is not None and str(output) != "-"
                else Path.cwd()
            )
            poster, previews, failed = _artwork(
                client, movie, folder, reference_folder, args.overwrite
            )
        document = (
            to_json(movie)
            if args.json
            else to_nfo(movie, poster=poster, fanart=previews)
        )
        _emit(document, output, args.overwrite)
        return 1 if failed else 0
    except (JavDBError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        return 130
