# javdb-python

Search [JAVDatabase](https://www.javdatabase.com/) and export movie metadata as
Kodi-compatible NFO or JSON. Posters and preview images can also be downloaded.

## Install

```bash
pip install javdb
```

## Usage

```bash
# Interactive search
javdb

# Search by movie ID and save an NFO
javdb -q SONE-763 -o SONE-763.nfo

# Save JSON instead
javdb -q SONE-763 --json -o metadata.json

# Skip search and download artwork directly
javdb -l https://www.javdatabase.com/movies/sone-763/ -d
```

## Options

- `-q, --query` — movie ID or search text
- `-l, --link` — direct movie page URL
- `-o, --output` — output file path
- `-d, --download` — download the poster and previews
- `--json` — output JSON instead of NFO

NFO output includes the title, IDs, release date, runtime, studio, director,
series, genres, actresses, plot, and artwork references when available.

Run `javdb --help` for the complete command help.

## License

MIT
