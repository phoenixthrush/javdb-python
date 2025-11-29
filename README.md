# javdb-python

Python API wrapper for javdatabase.com. Search movies, extract metadata, and download preview images.

## Installation

```bash
pip install requests beautifulsoup4
```

## Usage

### Basic Search

Search for a movie by ID or title and interactively select from results:

```bash
python ./main.py 
python ./main.py --query SONE-763
```

### Search with Download

Search and download preview images to `dvd_id/preview/`:

```bash
python ./main.py --query SONE-763 --download
```

### Search with JSON Output

Search and save metadata to a file:

```bash
python ./main.py --query SONE-763 --output metadata.json
```

### Direct Link

Skip the search and go directly to a movie page:

```bash
python ./main.py --link https://www.javdatabase.com/movies/sone-763/
```

### Direct Link with Download

Download preview images directly from a movie URL:

```bash
python ./main.py --link https://www.javdatabase.com/movies/sone-763/ --download
```

### All Options Combined

Search, save metadata, and download images:

```bash
python ./main.py --query SONE-763 --output metadata.json --download
```

## Options

- `--query, -q`: Search query (e.g., video ID or title)
- `--link, -l`: Direct URL to movie page (skips search)
- `--output, -o`: Output file path (saves metadata as JSON)
- `--download, -d`: Download preview images to `dvd_id/preview/`

## Output

When you search for a movie, metadata is extracted and displayed as JSON:

```json
{
    "link": "https://www.javdatabase.com/movies/sone-763/",
    "title": "SONE-763 -  A quiet and intelligent beauty is trained to be a real dick - Ayaka Kawakita",
    "jav_series": null,
    "dvd_id": "SONE-763",
    "content_id": "sone00763",
    "release_date": "2025-06-20",
    "runtime": "160 min.",
    "studio": "S1 NO.1 STYLE",
    "director": "Hironori Takase",
    "genres": [
        "4K",
        "Cowgirl",
        "Dirty Talk",
        "Drama",
        "Exclusive Distribution",
        "Featured Actress",
        "Hi-Def",
        "Slut",
        "Various Worker"
    ],
    "actresses": [
        "Saika Kawakita"
    ],
    "preview_images": [
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-1.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-2.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-3.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-4.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-5.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-6.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-7.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-8.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-9.jpg",
        "https://pics.dmm.co.jp/digital/video/sone00763/sone00763jp-10.jpg"
    ]
}
```

When `--download` is used, a JSON copy is automatically saved to `dvd_id/content_id.json`.

## Extracted Metadata

The tool extracts the following information:

- **Title**: Full movie title
- **JAV Series**: Series name (if applicable)
- **DVD ID**: Product ID (e.g., SONE-763)
- **Content ID**: Content identifier (e.g., sone00763)
- **Release Date**: Release date
- **Runtime**: Duration in minutes
- **Studio**: Production studio
- **Director**: Director (if available)
- **Genres**: List of genre tags
- **Actresses**: List of actresses/idols
- **Preview Images**: URLs to preview images for download

## License

MIT License

See [LICENSE](LICENSE) for details.
