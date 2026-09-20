import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree

from javdb import Client, JavDBError, parse_movie, parse_search, to_nfo
from javdb.cli import main
from javdb.export import atomic_file, movie_basename, safe_filename, write_text

FIXTURES = Path(__file__).parent / "fixtures"
MOVIE_HTML = (FIXTURES / "movie.html").read_text()
SEARCH_HTML = (FIXTURES / "search.html").read_text()
URL = "https://www.javdatabase.com/movies/abc-123/"


class Response(io.BytesIO):
    def __init__(self, content, content_type="text/html; charset=utf-8"):
        super().__init__(content)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class ParsingTests(unittest.TestCase):
    def test_nested_search_cards_quotes_order_and_duplicates(self):
        results = parse_search(SEARCH_HTML)
        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0],
            {
                "code": "ABC-123",
                "title": "A & B",
                "link": URL,
                "date": "2024-01-02",
                "studio": "Example Studio",
                "poster": "https://www.javdatabase.com/thumb.jpg",
            },
        )

    def test_movie_fields_and_artwork(self):
        movie = parse_movie(MOVIE_HTML, URL)
        self.assertEqual(movie["dvd_id"], "ABC-123")
        self.assertEqual(movie["release_date"], "2024-01-02")
        self.assertEqual(movie["runtime"], "2 hours 10 minutes")
        self.assertEqual(
            movie["studio"], "A Very Long Studio Name That Must Never Be Cut Short"
        )
        self.assertEqual(movie["genres"], ["Drama", "Comedy"])
        self.assertEqual(movie["actresses"], ["Example One", "Example Two"])
        self.assertEqual(
            movie["poster"], "https://www.javdatabase.com/images/cover.jpg"
        )
        self.assertEqual(
            movie["preview_images"],
            [
                "https://cdn.example.com/full/1.jpg",
                "https://www.javdatabase.com/small/2.jpg",
            ],
        )
        self.assertEqual(
            movie["plot"],
            "First paragraph with formatting & entities.\n\nSecond paragraph.",
        )

    def test_missing_values_and_unsafe_urls(self):
        movie = parse_movie(
            "<h1>Title</h1><a data-image-src='javascript:alert(1)'></a>"
        )
        self.assertIsNone(movie["dvd_id"])
        self.assertEqual(movie["genres"], [])
        self.assertEqual(movie["preview_images"], [])

    def test_fallback_poster_and_plain_genres(self):
        movie = parse_movie(
            "<meta property='og:image' content='/cover.jpg'><p><b>Genres:</b> Drama; Comedy</p>"
        )
        self.assertEqual(movie["genres"], ["Drama", "Comedy"])
        self.assertEqual(movie["poster"], "https://www.javdatabase.com/cover.jpg")

    def test_plot_without_paragraph_wrappers(self):
        html = "<div><h4>About ABC-123 JAV Movie</h4>First sentence.\nSecond sentence."
        html += "JAV Database only provides official links.<br><div>Ratings</div></div>"
        self.assertEqual(
            parse_movie(html)["plot"], "First sentence.\n\nSecond sentence."
        )

    def test_search_title_excludes_date_and_ads(self):
        html = SEARCH_HTML.replace("</a></div>", "</a>2024-01-02</div>", 1)
        html += "<div class='borderlesscard'><p class='pcard'><a href='https://ads.example/movies/ad/'>Ad</a></p></div>"
        html += "<div class='borderlesscard'><p class='pcard'><a href='/movies/ad/' rel='sponsored'>Ad</a></p></div>"
        results = parse_search(html)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "A & B")

    def test_explicit_title_and_jav_series_label(self):
        html = (
            MOVIE_HTML
            + "<p><b>Title:</b>Actual title</p><p><b>JAV Series:</b><a>Actual series</a></p>"
        )
        movie = parse_movie(html)
        self.assertEqual(movie["title"], "Actual title")
        self.assertEqual(movie["series"], "Actual series")
        self.assertNotIn("jav_series", movie)

    def test_table_movie_all_fields(self):
        html = (FIXTURES / "table-movie.html").read_text()
        movie = parse_movie(html, URL)
        self.assertEqual(
            movie,
            {
                "link": URL,
                "title": "Actual title",
                "dvd_id": None,
                "content_id": None,
                "series": None,
                "studio": "Example Studio",
                "release_date": "2025-10-16",
                "runtime": "61 min.",
                "genres": ["Drama", "Comedy"],
                "actresses": ["Example Person"],
                "director": None,
                "plot": None,
                "poster": "https://www.javdatabase.com/cover.webp",
                "trailer": "https://www.javdatabase.com/sample.mp4",
                "preview_images": [],
                "rating": {"value": 4.25, "max": 5.0, "votes": 1234},
            },
        )
        root = ElementTree.fromstring(to_nfo(movie))
        self.assertEqual(root.findtext("trailer"), movie["trailer"])
        self.assertEqual(root.findtext("ratings/rating/value"), "4.25")
        self.assertEqual(root.findtext("ratings/rating/votes"), "1234")

    def test_unrated_movie_has_no_rating_or_widget_plot(self):
        html = (
            (FIXTURES / "table-movie.html")
            .read_text()
            .replace("4.25/5 from 1,234 votes", "No Ratings Yet")
        )
        movie = parse_movie(html)
        self.assertIsNone(movie["rating"])
        self.assertIsNone(movie["plot"])
        self.assertIsNone(ElementTree.fromstring(to_nfo(movie)).find("ratings"))

    def test_nfo_roundtrip(self):
        movie = parse_movie(MOVIE_HTML, URL)
        movie["title"] += "\x01"
        root = ElementTree.fromstring(to_nfo(movie))
        self.assertEqual(root.findtext("title"), "ABC-123 A & B")
        self.assertEqual(root.findtext("runtime"), "130")
        self.assertEqual(root.findtext("set/name"), "Collection & Stories")
        self.assertEqual(root.findtext("fanart/thumb"), movie["preview_images"][0])
        self.assertEqual(root.findtext("actor/name"), "Example One")
        local = ElementTree.fromstring(
            to_nfo(movie, poster="artwork/poster.jpg", fanart=[])
        )
        self.assertEqual(local.findtext("thumb"), "artwork/poster.jpg")
        self.assertIsNone(local.find("fanart"))


class FileTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename("../../a:b"), "a-b")
        self.assertEqual(safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_filename("..."), "movie")

    def test_atomic_writes_refuse_replace_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "file.json"
            write_text(target, "old")
            with self.assertRaises(FileExistsError):
                write_text(target, "new")
            with (
                self.assertRaises(RuntimeError),
                atomic_file(target, overwrite=True) as output,
            ):
                output.write(b"partial")
                raise RuntimeError("interrupted")
            self.assertEqual(target.read_text(), "old\n")
            self.assertEqual(list(target.parent.iterdir()), [target])
            write_text(target, "new", overwrite=True)
            self.assertEqual(target.read_text(), "new\n")

    def test_video_matching_requires_unambiguous_id(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "other.mkv").touch()
            (folder / "ABC-1234.mp4").touch()
            self.assertEqual(movie_basename(folder, "ABC-123", "ABC-123"), "ABC-123")
            (folder / "ABC-123 [HD].mkv").touch()
            self.assertEqual(
                movie_basename(folder, "ABC-123", "ABC-123"), "ABC-123 [HD]"
            )
            (folder / "ABC-123 [SD].mp4").touch()
            self.assertEqual(movie_basename(folder, "ABC-123", "ABC-123"), "ABC-123")


class ClientTests(unittest.TestCase):
    @patch("javdb.client.time.sleep")
    @patch("javdb.client.urlopen")
    def test_retry_and_timeout(self, open_url, sleep):
        open_url.side_effect = [URLError("temporary"), Response(MOVIE_HTML.encode())]
        movie = Client(timeout=3, retries=1).movie(URL)
        self.assertEqual(movie["dvd_id"], "ABC-123")
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 3)
        sleep.assert_called_once_with(1)

    @patch("javdb.client.urlopen")
    def test_http_404_not_retried(self, open_url):
        open_url.side_effect = HTTPError(URL, 404, "not found", {}, io.BytesIO())
        with self.assertRaisesRegex(JavDBError, "HTTP 404"):
            Client().movie(URL)
        self.assertEqual(open_url.call_count, 1)

    @patch("javdb.client.time.sleep")
    @patch("javdb.client.urlopen")
    def test_retry_exhaustion(self, open_url, sleep):
        open_url.side_effect = URLError("offline")
        with self.assertRaises(JavDBError):
            Client(retries=2).movie(URL)
        self.assertEqual(open_url.call_count, 3)

    @patch("javdb.client.urlopen")
    def test_block_page_is_failure(self, open_url):
        open_url.return_value = Response(b"<h1>Access denied</h1>")
        with self.assertRaisesRegex(JavDBError, "No movie metadata"):
            Client().movie(URL)

    @patch("javdb.client.urlopen")
    def test_table_movie_without_identifiers_is_valid(self, open_url):
        open_url.return_value = Response((FIXTURES / "table-movie.html").read_bytes())
        movie = Client().movie(URL)
        self.assertEqual(movie["studio"], "Example Studio")
        self.assertIsNone(movie["dvd_id"])

    def test_validation(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Client(timeout=timeout)
        with self.assertRaises(ValueError):
            Client(retries=-1)
        with self.assertRaises(JavDBError):
            Client().movie("file:///etc/passwd")

    @patch("javdb.client.urlopen")
    def test_pagination_deduplicates_and_stops(self, open_url):
        open_url.side_effect = [
            Response(SEARCH_HTML.encode()),
            Response(SEARCH_HTML.encode()),
        ]
        self.assertEqual(len(Client().search("a & b", page=2, pages=4)), 2)
        self.assertEqual(open_url.call_count, 2)
        self.assertIn("paged=2", open_url.call_args_list[0].args[0].full_url)
        self.assertIn("s=a+%26+b", open_url.call_args_list[0].args[0].full_url)

    @patch("javdb.client.urlopen")
    def test_download_and_content_type(self, open_url):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "poster.jpg"
            open_url.return_value = Response(b"<html>blocked</html>")
            with self.assertRaises(JavDBError):
                Client().download(URL, target)
            self.assertFalse(target.exists())
            open_url.return_value = Response(b"image bytes", "image/jpeg")
            Client().download(URL, target)
            self.assertEqual(target.read_bytes(), b"image bytes")

    @patch("javdb.client.time.sleep")
    @patch("javdb.client.urlopen")
    def test_truncated_download_is_retried_without_partial_file(self, open_url, sleep):
        truncated = Response(b"short", "image/jpeg")
        truncated.headers["Content-Length"] = "100"
        open_url.side_effect = [truncated, Response(b"complete", "image/jpeg")]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "poster.jpg"
            Client(retries=1).download(URL, target)
            self.assertEqual(target.read_bytes(), b"complete")
            self.assertEqual(list(Path(directory).iterdir()), [target])
            self.assertEqual(open_url.call_count, 2)

    @patch("javdb.client.urlopen")
    def test_empty_image_preserves_existing_file(self, open_url):
        open_url.return_value = Response(b"", "image/jpeg")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "poster.jpg"
            target.write_bytes(b"old")
            with self.assertRaisesRegex(JavDBError, "Empty image"):
                Client().download(URL, target, overwrite=True)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    @patch("javdb.client.time.sleep")
    @patch("javdb.client.urlopen")
    def test_server_failure_is_retried(self, open_url, sleep):
        open_url.side_effect = [
            HTTPError(URL, 503, "unavailable", {}, io.BytesIO()),
            Response(MOVIE_HTML.encode()),
        ]
        self.assertEqual(Client(retries=1).movie(URL)["dvd_id"], "ABC-123")
        self.assertEqual(open_url.call_count, 2)


class CLITests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    @patch("javdb.cli.Client")
    def test_json_stdout_is_clean(self, client):
        client.return_value.movie.return_value = parse_movie(MOVIE_HTML, URL)
        code, out, err = self.invoke(["-l", URL, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["dvd_id"], "ABC-123")
        self.assertEqual(err, "")

    @patch("javdb.cli.Client")
    def test_search_only_json(self, client):
        client.return_value.search.return_value = parse_search(SEARCH_HTML)
        code, out, _err = self.invoke(
            ["-q", "example", "--search", "--json", "--pages", "2"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 2)
        client.return_value.movie.assert_not_called()

    @patch("javdb.cli.sys.stdin.isatty", return_value=False)
    @patch("javdb.cli.Client")
    def test_ambiguous_noninteractive_search(self, client, isatty):
        client.return_value.search.return_value = parse_search(SEARCH_HTML)
        code, out, err = self.invoke(["-q", "example"])
        self.assertEqual(code, 1)
        self.assertIn("--select", err)
        self.assertEqual(out, "")
        client.return_value.movie.assert_not_called()

    @patch("javdb.cli.Client")
    def test_exact_match_and_explicit_selection(self, client):
        client.return_value.search.return_value = parse_search(SEARCH_HTML)
        client.return_value.movie.return_value = parse_movie(MOVIE_HTML, URL)
        self.assertEqual(self.invoke(["-q", "abc-123"])[0], 0)
        client.return_value.movie.assert_called_with(URL)
        self.assertEqual(self.invoke(["-q", "abc", "--select", "2"])[0], 0)
        client.return_value.movie.assert_called_with(
            "https://www.javdatabase.com/movies/abc-124/"
        )
        self.assertEqual(self.invoke(["-q", "abc", "--select", "3"])[0], 1)

    @patch("javdb.cli.Client")
    def test_file_output_and_overwrite(self, client):
        client.return_value.movie.return_value = parse_movie(MOVIE_HTML, URL)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "movie.json"
            args = ["-l", URL, "--json", "-o", str(output)]
            code, out, _err = self.invoke(args)
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(json.loads(output.read_text())["dvd_id"], "ABC-123")
            self.assertEqual(self.invoke(args)[0], 1)
            self.assertEqual(self.invoke(args + ["--overwrite"])[0], 0)

    @patch("javdb.cli.Client")
    def test_partial_artwork_failure_keeps_metadata(self, client):
        client.return_value.movie.return_value = parse_movie(MOVIE_HTML, URL)
        client.return_value.download.side_effect = JavDBError("failed")
        with tempfile.TemporaryDirectory() as directory:
            code, _out, _err = self.invoke(["-l", URL, "-d", "--directory", directory])
            self.assertEqual(code, 1)
            root = ElementTree.parse(Path(directory) / "ABC-123.nfo")
            self.assertEqual(
                root.findtext("thumb"), "https://www.javdatabase.com/images/cover.jpg"
            )
            self.assertEqual(client.return_value.download.call_count, 3)

    @patch("javdb.cli.Client")
    def test_artwork_references_relative_to_explicit_output(self, client):
        client.return_value.movie.return_value = parse_movie(MOVIE_HTML, URL)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            output = folder / "metadata" / "movie.nfo"
            code, _out, _err = self.invoke(
                [
                    "-l",
                    URL,
                    "-d",
                    "--directory",
                    str(folder / "images"),
                    "-o",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)
            root = ElementTree.parse(output)
            self.assertEqual(root.findtext("thumb"), "../images/artwork/poster.jpg")

    def test_invalid_arguments(self):
        for args in (
            ["--timeout", "nan"],
            ["--pages", "0"],
            ["--retries", "-1"],
            ["--search", "-d"],
            ["-q", "abc", "-l", URL],
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
