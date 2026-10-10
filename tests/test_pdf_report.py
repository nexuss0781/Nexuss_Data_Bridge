from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from telegram_file_bridge.reports import (
    build_pdf_report,
    download_pdf_files,
    render_markdown_report,
    scan_pdf_files,
    write_pdf_report,
)


def make_message(
    message_id: int,
    file_name: str,
    *,
    payload: bytes = b"placeholder",
    caption: str = "",
    mime_type: str | None = "application/pdf",
    document_id: int | None = None,
):
    document = SimpleNamespace(
        id=document_id if document_id is not None else message_id + 1000,
        mime_type=mime_type,
        size=len(payload),
        attributes=[],
    )
    return SimpleNamespace(
        id=message_id,
        chat_id=-100123,
        media=SimpleNamespace(document=document),
        document=document,
        file=SimpleNamespace(name=file_name, size=len(payload), mime_type=mime_type),
        message=caption,
        date=datetime(2025, 1, 2, tzinfo=timezone.utc),
        payload=payload,
    )


class FakeClient:
    def __init__(self, messages):
        self.messages = list(messages)
        self.downloaded_ids: list[int] = []

    def iter_messages(self, chat, limit=None):
        async def iterator():
            selected = self.messages if limit is None else self.messages[:limit]
            for message in reversed(selected):
                yield message

        return iterator()

    async def get_messages(self, chat, ids):
        return next((message for message in self.messages if message.id == ids), None)

    async def download_media(self, message, file):
        Path(file).write_bytes(message.payload)
        self.downloaded_ids.append(message.id)
        return file


def make_pdf(title: str, author: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": title, "/Author": author, "/Subject": "A test book"})
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "fixture.pdf"
        with path.open("wb") as stream:
            writer.write(stream)
        return path.read_bytes()


class PdfReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_scans_full_history_filters_pdfs_and_sorts_similar_names_together(self):
        messages = [
            make_message(3, "book-one.pdf", caption="Second copy"),
            make_message(2, "notes.txt", mime_type="text/plain"),
            make_message(1, "Book One.PDF", caption="First caption"),
            make_message(4, "annual report", mime_type="application/pdf"),
            make_message(5, "../../unsafe/Other.PDF"),
        ]
        client = FakeClient(messages)

        records = await scan_pdf_files(client, "@books")

        self.assertEqual([record.message_id for record in records], [4, 1, 3, 5])
        self.assertEqual(records[1].normalized_name, records[2].normalized_name)
        self.assertEqual(records[1].caption, "First caption")
        self.assertEqual(records[0].file_name, "annual report")
        self.assertEqual(records[3].file_name, "Other.PDF")
        self.assertEqual(records[1].message_date, "2025-01-02T00:00:00+00:00")

    async def test_message_limit_is_validated_and_respected(self):
        client = FakeClient([make_message(1, "a.pdf"), make_message(2, "b.pdf")])
        self.assertEqual(len(await scan_pdf_files(client, "@books", limit=1)), 1)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            await scan_pdf_files(client, "@books", limit=0)

    async def test_download_inspects_pdf_metadata_and_identifies_exact_duplicate_bytes(self):
        payload = make_pdf("Book metadata title", "Jane Author")
        messages = [
            make_message(11, "metadata-title.pdf", payload=payload, caption="Original channel caption"),
            make_message(12, "metadata title.PDF", payload=payload, caption="Another caption"),
        ]
        client = FakeClient(messages)
        with tempfile.TemporaryDirectory() as temp_dir:
            records = await scan_pdf_files(client, "@books")
            enriched = await download_pdf_files(
                client,
                "@books",
                records,
                Path(temp_dir) / "pdfs",
                report_root=temp_dir,
            )
            report = build_pdf_report("@books", enriched)

            self.assertEqual(client.downloaded_ids, [12, 11])
            self.assertEqual(enriched[0].book_title, "Book metadata title")
            self.assertEqual(enriched[0].title_source, "pdf_metadata")
            self.assertEqual(enriched[0].pdf_author, "Jane Author")
            self.assertEqual(enriched[0].page_count, 1)
            enriched_by_id = {item.message_id: item for item in enriched}
            self.assertEqual(enriched_by_id[11].caption, "Original channel caption")
            self.assertEqual(enriched_by_id[12].caption, "Another caption")
            self.assertEqual(report["summary"]["identical_content_groups"], 1)
            self.assertEqual(report["identical_content_groups"][0]["message_ids"], [11, 12])
            self.assertTrue(enriched_by_id[11].sha256)
            self.assertTrue((Path(temp_dir) / enriched_by_id[11].local_path).is_file())

    async def test_writes_complete_json_and_markdown_reports(self):
        records = await scan_pdf_files(
            FakeClient([make_message(9, "Evidence.pdf", caption="Book caption\nSecond line")]),
            "@books",
        )
        report = build_pdf_report("@books", records)
        markdown = render_markdown_report(report)
        self.assertIn("Book caption", markdown)
        self.assertIn("Second line", markdown)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_pdf_report(report, temp_dir, "test-report", report_format="both")
            data = json.loads((Path(temp_dir) / "test-report.json").read_text(encoding="utf-8"))
            self.assertEqual([path.suffix for path in paths], [".json", ".md"])
            self.assertEqual(data["files"][0]["caption"], "Book caption\nSecond line")
            self.assertEqual((Path(temp_dir) / "test-report.md").read_text(encoding="utf-8"), markdown)
            self.assertEqual((Path(temp_dir) / "test-report.json").stat().st_mode & 0o777, 0o600)

    def test_cli_report_format_is_available_to_dispatch(self):
        from telegram_file_bridge.cli import _parser

        args = _parser().parse_args(["report", "--chat", "@books", "--format", "json"])
        self.assertEqual(args.report_format, "json")


if __name__ == "__main__":
    unittest.main()
