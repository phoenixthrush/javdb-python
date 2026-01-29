# javdb-python

Command-line scraper for javdatabase.com. Search movies, extract metadata, and
download preview images or NFO files for media centers.

## Installation

```bash
pip install javdb
```

## Usage

### Basic Search

Search for a movie by ID or title and interactively select from results:

```bash
javdb
```

```bash
javdb --query SONE-763
```

### Search with NFO Output

Search and save metadata to a Kodi-compatible NFO file:

```bash
javdb --query SONE-763 --output SONE-763.nfo
```

### Search with JSON Output

Search and save metadata as JSON:

```bash
javdb --query SONE-763 --json --output metadata.json
```

### Direct Link

Skip the search and go directly to a movie page:

```bash
javdb --link https://www.javdatabase.com/movies/sone-763/
```

### Direct Link with Download

Download preview images directly from a movie URL:

```bash
javdb --link https://www.javdatabase.com/movies/sone-763/ --download
```

### All Options Combined

Search, save metadata, and download images:

```bash
javdb --query SONE-763 --output SONE-763.nfo --download
```

## Options

- `--query, -q`: Search query (e.g., video ID or title)
- `--link, -l`: Direct URL to movie page (skips search)
- `--output, -o`: Output file path (NFO/XML by default, or JSON when `--json` is used)
- `--download, -d`: Download poster and preview images to `dvd_id/preview/` and write NFO there
- `--json`: Output metadata as JSON instead of NFO/XML

## Extracted Metadata

The tool scrapes javdatabase.com and writes a Kodi-style `movie.nfo` XML with:

- **Title**
- **Series** (when available)
- **DVD ID** and **Content ID**
- **Release Date** and **Runtime**
- **Studio** and **Director**
- **Genres** and **Actresses/Idols**
- Optional poster and fanart references when `--download` is used

When `--json` is used, the same metadata is returned as a JSON object with
keys such as `title`, `jav_series`, `dvd_id`, `content_id`, `release_date`,
`runtime`, `studio`, `director`, `genres`, `actresses`, `preview_images`,
and `poster`.

## Programmatic Use (experimental)

You can also call the scraping helpers from Python code:

```python
from javdb.__main__ import fetch_movie_metadata, fetch_preview_images, fetch_poster_url

url = "https://www.javdatabase.com/movies/sone-763/"
meta = fetch_movie_metadata(url)
previews = fetch_preview_images(url)
poster = fetch_poster_url(url)
```

The functions and HTML structure are not considered a stable API, but this can
be handy for quick scripts.

## License

MIT License

See [LICENSE](LICENSE) for details.
