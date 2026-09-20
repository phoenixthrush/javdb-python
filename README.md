# javdb-python

Search [JAVDatabase](https://www.javdatabase.com/), export movie metadata as
JSON or Kodi NFO, and download posters and preview images.

Requires **Python 3.11+**. No runtime dependencies.

## Install

```sh
python -m pip install javdb
```

From a local checkout:

```sh
python -m pip install .
```

## CLI

Use `javdb` or `python -m javdb`.

```sh
# Interactive search
javdb

# Save movie metadata as NFO or JSON
javdb -q SONE-763 -o movie.nfo
javdb -q SONE-763 --json -o movie.json

# Fetch a movie directly
javdb -l https://www.javdatabase.com/movies/sone-763/ --json

# List search results across up to three pages
javdb -q 'search words' --search --json --pages 3

# Select a result without prompting
javdb -q 'search words' --first --json
javdb -q 'search words' --select 2 -o movie.nfo

# Save metadata and artwork to a directory
javdb -q SONE-763 -d --directory ./movies/SONE-763
```

A single result or exact movie ID match is selected automatically. Otherwise,
choose a numbered result interactively, or use `--first` / `--select N` in scripts.

| Option | Description |
| --- | --- |
| `-q, --query TEXT` | Movie ID or search text |
| `-l, --link URL` | Direct movie URL; use instead of `--query` |
| `-o, --output FILE` | Output file; `-` writes to stdout |
| `--json` | Export JSON instead of NFO |
| `--search` | List results without fetching movie details |
| `--first` | Select the first result |
| `--select N` | Select a result by its 1-based index |
| `--page N` | First search page; default `1` |
| `--pages N` | Maximum pages to search; default `1` |
| `-d, --download` | Download poster and preview images |
| `--directory DIR` | Directory for metadata and artwork |
| `--overwrite` | Replace existing files |
| `--timeout SECONDS` | Socket timeout per attempt; default `15` |
| `--retries N` | Retries after transient failures; default `2` |
| `--version` | Show version |
| `-h, --help` | Show help |

`--first` and `--select` are mutually exclusive. Pagination and selection apply
to searches. `--search` supports query, pagination, JSON, and output options;
it cannot be combined with direct links, selection, or artwork/directory options.

### Output

Metadata goes to stdout unless a file or directory is specified. Progress and
errors go to stderr. `-o FILE` writes only to that file; `-o -` forces stdout.

`--directory DIR` saves metadata there, with or without artwork. With `-d` alone,
the directory is `./<DVD-ID>/`, falling back to a sanitized title. Default files:

```text
SONE-763/
  SONE-763.nfo          # metadata.json with --json
  artwork/
    poster.jpg
    preview-001.jpg
    preview-002.jpg
```

Image extensions follow their URLs. If exactly one video in the chosen directory
matches the DVD ID, its filename stem is used for the NFO. `-o` overrides the
metadata path independently of the artwork directory.

Existing metadata requires `--overwrite`; existing artwork is reused unless
that flag is supplied. NFO uses local paths for downloaded artwork and remote
URLs otherwise. JSON always keeps remote URLs. Trailer URLs are included as
metadata; only images are downloaded.

Exit codes: `0` success, `1` request/parsing/file failure, `2` invalid arguments,
`130` cancellation. A failed image download still allows metadata to be saved
with its remote URL, but returns `1`.

## Python API

```python
from javdb import Client, JavDBError, to_json, to_nfo

client = Client(timeout=15, retries=2)

try:
    results = client.search("SONE-763", page=1, pages=2)
    if results:
        movie = client.movie(results[0]["link"])
        print(to_json(movie))
        print(to_nfo(movie))

        if movie["poster"]:
            client.download(movie["poster"], "poster.jpg", overwrite=False)
except (JavDBError, OSError, ValueError) as error:
    print(error)
```

| Function | Returns / behavior |
| --- | --- |
| `Client(timeout=15, retries=2)` | Create a client |
| `client.search(query, page=1, pages=1)` | List of result dictionaries |
| `client.movie(url)` | Movie dictionary |
| `client.download(url, destination, overwrite=False)` | Save an image, creating parent directories |
| `parse_search(html, base_url=...)` | Parse search HTML without a request |
| `parse_movie(html, page_url=...)` | Parse movie HTML without a request |
| `to_json(value)` | JSON string for a movie or result list |
| `to_nfo(movie, poster=None, fanart=None)` | NFO string; optionally supply local artwork paths |

Import these classes and functions directly from `javdb`. Pass the source URL
to the HTML parsers to resolve relative links; the default is
`https://www.javdatabase.com/`. Standalone parsers allow incomplete metadata;
`client.movie()` raises `JavDBError` for unrecognized movie pages.

Network/parsing failures raise `JavDBError`, invalid parameters raise `ValueError`,
and file errors raise `OSError` subclasses. Library calls do not print or prompt.

### Returned data

Search results contain `code`, `title`, `link`, `date`, `studio`, and `poster`
(cover thumbnail URL). For entries without a DVD ID, `code` is the card heading.

Movie dictionaries contain:

| Keys | Values |
| --- | --- |
| `link`, `title` | Page URL and movie title |
| `dvd_id`, `content_id` | Movie identifiers |
| `series`, `studio`, `director` | Names |
| `release_date`, `runtime` | Date and runtime text supplied by the site |
| `plot` | About-section text |
| `genres`, `actresses` | Lists of names |
| `poster`, `trailer` | Image and sample video URLs |
| `preview_images` | List of image URLs, preferring full-size images |
| `rating` | Object with `value`, `max`, and `votes` |

Missing values are `None` (`null` in JSON); missing lists are empty. Unrated
movies have `rating=None`. NFO omits unavailable fields and converts runtime
to minutes.

## Development

Run the offline tests with no additional dependencies:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
