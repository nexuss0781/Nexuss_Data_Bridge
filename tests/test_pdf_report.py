from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from telegram_file_bridge.reports import (
    build_pdf_report,
    download_pdf_files,
    render_markdown_report,
    scan_pdf_files,
    select_forward_candidates,
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
        filtered = build_pdf_report("@books", records, exclude_byte_identical_details=True)
        self.assertEqual(filtered["detailed_comparison_message_ids"], [4, 1, 3, 5])
        self.assertEqual(filtered["summary"]["excluded_byte_identical_details"], 0)
        self.assertTrue(all(item["content_category"] == "not_compared" for item in filtered["files"]))

    async def test_message_limit_is_validated_and_respected(self):
        client = FakeClient([make_message(1, "a.pdf"), make_message(2, "b.pdf")])
        self.assertEqual(len(await scan_pdf_files(client, "@books", limit=1)), 1)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            await scan_pdf_files(client, "@books", limit=0)

    async def test_duplicate_priority_and_boolean_state_columns(self):
        records = await scan_pdf_files(
            FakeClient(
                [
                    make_message(31, "Z-duplicate.pdf"),
                    make_message(32, "A-unverified.pdf"),
                    make_message(33, "B-duplicate.pdf"),
                ]
            ),
            "@books",
        )
        records = [
            replace(record, sha256="a" * 64) if record.message_id in {31, 33} else record
            for record in records
        ]

        report = build_pdf_report("@books", records)

        self.assertEqual([item["message_id"] for item in report["files"]], [33, 31, 32])
        by_id = {item["message_id"]: item for item in report["files"]}
        self.assertTrue(by_id[31]["duplicate"])
        self.assertTrue(by_id[33]["duplicate"])
        self.assertFalse(by_id[32]["duplicate"])
        self.assertTrue(all(item["status"] is True for item in report["files"]))
        self.assertTrue(all(item["processed"] is False for item in report["files"]))
        self.assertEqual(report["summary"]["duplicate_files"], 2)
        self.assertEqual(report["summary"]["status_enabled_files"], 3)
        self.assertEqual(report["summary"]["processed_files"], 0)
        self.assertEqual(
            [item["message_id"] for item in select_forward_candidates(report)],
            [33, 31, 32],
        )

        changed = [
            replace(record, status=False) if record.message_id == 33 else
            replace(record, processed=True) if record.message_id == 32 else
            record
            for record in records
        ]
        changed_report = build_pdf_report("@books", changed)
        changed_by_id = {item["message_id"]: item for item in changed_report["files"]}
        self.assertFalse(changed_by_id[33]["status"])
        self.assertTrue(changed_by_id[32]["processed"])
        self.assertEqual([item["message_id"] for item in select_forward_candidates(changed_report)], [31])

        markdown = render_markdown_report(report)
        header = "| Duplicate | Status | Processed | File | Book/title | Telegram message ID | Date | Size (bytes) | PDF details | Caption |"
        self.assertIn(header, markdown)
        main = markdown.split("## PDF inventory (duplicates first, then sorted by name)\n", 1)[1].split(
            "## Byte-identical files — data scientist review", 1
        )[0]
        rows = [line for line in main.splitlines() if line.startswith("| ") and not line.startswith("|---") and line != header]
        self.assertEqual([line.split("|")[1].strip() for line in rows], ["True", "True", "False"])

    async def test_pdf_scan_does_not_turn_nameless_photos_or_non_pdf_documents_into_pdfs(self):
        nameless_photo = SimpleNamespace(
            id=81,
            media=SimpleNamespace(photo=object()),
            document=None,
            file=SimpleNamespace(name=None, size=321, mime_type="image/jpeg"),
            message="channel photo",
            date=None,
        )
        messages = [
            make_message(82, "scan.pdf", mime_type="image/jpeg"),
            make_message(83, "real.pdf", mime_type="application/pdf"),
            make_message(84, "unknown-type.pdf", mime_type=None),
            make_message(85, "   ", mime_type="image/jpeg"),
            nameless_photo,
        ]

        records = await scan_pdf_files(FakeClient(messages), "@books")

        self.assertEqual({record.message_id for record in records}, {83, 84})
        fallback_record = next(record for record in records if record.message_id == 84)
        self.assertEqual(fallback_record.file_name, "unknown-type.pdf")

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

            report_files = {item["message_id"]: item for item in report["files"]}
            self.assertEqual(report_files[11]["content_category"], "canonical_byte_identical_copy")
            self.assertEqual(report_files[12]["content_category"], "byte_identical_copy")
            self.assertEqual(report_files[12]["identical_to_message_id"], 11)
            self.assertEqual(len(report["files"]), 2)
            self.assertEqual(len(report["byte_identical_files_at_end"]), 1)
            self.assertEqual(
                [item["message_id"] for item in report["byte_identical_files_at_end"][0]["files"]],
                [11, 12],
            )
            full_markdown = render_markdown_report(report)
            review_heading = "## Byte-identical files — data scientist review"
            self.assertLess(full_markdown.index("## PDF inventory (duplicates first, then sorted by name)"), full_markdown.index(review_heading))
            self.assertIn("| Duplicate | Status | Processed | File | Book/title | Telegram message ID | Date | Size (bytes) | PDF details | Caption |", full_markdown)
            self.assertNotIn("Content category", full_markdown)
            full_main, full_review = full_markdown.split(review_heading, 1)
            self.assertIn("metadata title.PDF", full_main)
            self.assertIn("metadata-title.pdf", full_main)
            self.assertIn("| 11 |", full_review)
            self.assertIn("| 12 |", full_review)

            filtered = build_pdf_report("@books", enriched, exclude_byte_identical_details=True)
            filtered_by_id = {item["message_id"]: item for item in filtered["files"]}
            filtered_markdown = render_markdown_report(filtered)
            self.assertEqual(len(filtered["files"]), 2)
            self.assertEqual(filtered["summary"]["detailed_comparison_files"], 1)
            self.assertEqual(filtered["summary"]["excluded_byte_identical_details"], 1)
            self.assertEqual(filtered["detailed_comparison_message_ids"], [11])
            self.assertEqual(filtered["excluded_identical_message_ids"], [12])
            self.assertEqual(filtered_by_id[12]["caption"], "Another caption")
            self.assertIn("Byte-identical copies omitted from details: 1", filtered_markdown)
            filtered_main, filtered_review = filtered_markdown.split(review_heading, 1)
            self.assertIn("metadata-title.pdf", filtered_main)
            self.assertNotIn("metadata title.PDF", filtered_main)
            self.assertIn("metadata title.PDF", filtered_review)
            self.assertIn("Another caption", filtered_review)

    async def test_writes_complete_json_and_markdown_reports(self):
        records = await scan_pdf_files(
            FakeClient([make_message(9, "Evidence.pdf", caption="Book caption\nSecond line")]),
            "@books",
        )
        report = build_pdf_report("@books", records)
        report["analysis_scope"] = {
            "description": "Only duplicate-name candidates were downloaded.",
            "selected_group_count": 1,
            "selected_file_count": 2,
            "selected_size_bytes": 42,
            "telegram_file_filter_total": 3,
            "pdf_mime_count": 2,
            "non_pdf_document_count": 1,
        }
        markdown = render_markdown_report(report)
        self.assertIn("Book caption", markdown)
        self.assertIn("Second line", markdown)
        self.assertIn("Book caption ↵ Second line", markdown)
        self.assertIn("| Caption |", markdown)
        self.assertNotIn("Content category", markdown)
        self.assertIn("Only duplicate-name candidates were downloaded.", markdown)
        self.assertIn("Telegram Files/document filter count: 3", markdown)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_pdf_report(report, temp_dir, "test-report", report_format="both")
            data = json.loads((Path(temp_dir) / "test-report.json").read_text(encoding="utf-8"))
            self.assertEqual([path.suffix for path in paths], [".json", ".md"])
            self.assertEqual(data["files"][0]["caption"], "Book caption\nSecond line")
            self.assertEqual((Path(temp_dir) / "test-report.md").read_text(encoding="utf-8"), markdown)
            self.assertEqual((Path(temp_dir) / "test-report.json").stat().st_mode & 0o777, 0o600)

    def test_cli_report_format_is_available_to_dispatch(self):
        from telegram_file_bridge.cli import _parser

        args = _parser().parse_args(
            ["report", "--chat", "@books", "--format", "json", "--download-pdfs", "--exclude-byte-identical-details"]
        )
        self.assertEqual(args.report_format, "json")
        self.assertTrue(args.exclude_byte_identical_details)


if __name__ == "__main__":
    unittest.main()
