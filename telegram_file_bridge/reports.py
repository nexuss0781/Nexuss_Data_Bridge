from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .core import download_message


@dataclass(frozen=True)
class PdfFileRecord:
    message_id: int
    file_name: str
    normalized_name: str
    book_title: str
    title_source: str
    size_bytes: int | None
    mime_type: str | None
    message_date: str | None
    document_id: str | None
    caption: str
    local_path: str | None = None
    sha256: str | None = None
    pdf_title: str | None = None
    pdf_author: str | None = None
    pdf_subject: str | None = None
    page_count: int | None = None
    analysis_error: str | None = None


def _safe_report_filename(value: Any, message_id: int) -> str:
    raw = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw = "".join("_" if ord(char) < 32 or ord(char) == 127 else char for char in raw).strip()
    return raw if raw not in {"", ".", ".."} else f"telegram-{message_id}.pdf"


def _normalize_name(file_name: str) -> str:
    stem = Path(file_name).stem
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _date_string(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value is not None else None


def _clean_metadata(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _book_title(file_name: str, caption: str) -> tuple[str, str]:
    stem = Path(file_name).stem.strip()
    if stem and not re.fullmatch(r"telegram[-_ ]?\d+", stem, flags=re.IGNORECASE):
        return stem, "filename"
    first_caption_line = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    if first_caption_line:
        return first_caption_line[:180], "caption"
    return stem or "Untitled PDF", "fallback"


def _pdf_record(message: Any) -> PdfFileRecord | None:
    message_id = int(message.id)
    file_info = getattr(message, "file", None)
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)

    raw_name = getattr(file_info, "name", None)
    if not raw_name and document is not None:
        for attribute in getattr(document, "attributes", ()) or ():
            raw_name = getattr(attribute, "file_name", None)
            if raw_name:
                break
    file_name = _safe_report_filename(raw_name, message_id)

    mime_type = _clean_metadata(getattr(file_info, "mime_type", None))
    if mime_type is None and document is not None:
        mime_type = _clean_metadata(getattr(document, "mime_type", None))
    is_pdf = file_name.casefold().endswith(".pdf") or bool(
        mime_type and mime_type.casefold().split(";", 1)[0].strip() in {"application/pdf", "application/x-pdf"}
    )
    if not is_pdf:
        return None

    caption = getattr(message, "message", None) or ""
    size = getattr(file_info, "size", None)
    if not isinstance(size, int) and document is not None:
        size = getattr(document, "size", None)
    if not isinstance(size, int):
        size = None

    document_id = getattr(document, "id", None) if document is not None else None
    book_title, title_source = _book_title(file_name, str(caption))
    return PdfFileRecord(
        message_id=message_id,
        file_name=file_name,
        normalized_name=_normalize_name(file_name),
        book_title=book_title,
        title_source=title_source,
        size_bytes=size,
        mime_type=mime_type,
        message_date=_date_string(getattr(message, "date", None)),
        document_id=str(document_id) if document_id is not None else None,
        caption=str(caption),
    )


def _record_sort_key(record: PdfFileRecord) -> tuple[str, str, int]:
    return (record.normalized_name, record.file_name.casefold(), record.message_id)


async def scan_pdf_files(client: Any, chat: str, *, limit: int | None = None) -> list[PdfFileRecord]:
    """Scan Telegram message history and return every attachment identified as a PDF."""
    if limit is not None and limit <= 0:
        raise ValueError("The message limit must be a positive integer.")

    records: list[PdfFileRecord] = []
    async for message in client.iter_messages(chat, limit=limit):
        record = _pdf_record(message)
        if record is not None:
            records.append(record)
    return sorted(records, key=_record_sort_key)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_pdf(path: Path, record: PdfFileRecord) -> PdfFileRecord:
    digest = _sha256(path)
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path), strict=False)
        metadata = reader.metadata
        pdf_title = _clean_metadata(getattr(metadata, "title", None)) if metadata else None
        pdf_author = _clean_metadata(getattr(metadata, "author", None)) if metadata else None
        pdf_subject = _clean_metadata(getattr(metadata, "subject", None)) if metadata else None
        page_count = len(reader.pages)
        return replace(
            record,
            book_title=pdf_title or record.book_title,
            title_source="pdf_metadata" if pdf_title else record.title_source,
            sha256=digest,
            pdf_title=pdf_title,
            pdf_author=pdf_author,
            pdf_subject=pdf_subject,
            page_count=page_count,
        )
    except Exception as exc:
        return replace(record, sha256=digest, analysis_error=f"{type(exc).__name__}: {exc}"[:500])


async def download_pdf_files(
    client: Any,
    chat: str,
    records: list[PdfFileRecord],
    output_dir: str | Path,
    *,
    report_root: str | Path | None = None,
) -> list[PdfFileRecord]:
    """Download each discovered PDF and enrich its record with hash/PDF metadata."""
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(destination, 0o700)
    except OSError:
        pass
    root = Path(report_root).expanduser().resolve() if report_root is not None else destination

    enriched: list[PdfFileRecord] = []
    for record in records:
        try:
            result = await download_message(client, chat, record.message_id, destination)
            downloaded = Path(result.local_path or "").resolve()
            try:
                os.chmod(downloaded, 0o600)
            except OSError:
                pass
            try:
                relative_path = str(downloaded.relative_to(root))
            except ValueError:
                relative_path = str(downloaded)
            enriched.append(_inspect_pdf(downloaded, replace(record, local_path=relative_path)))
        except Exception as exc:
            enriched.append(replace(record, analysis_error=f"{type(exc).__name__}: {exc}"[:500]))
    return sorted(enriched, key=_record_sort_key)


def build_pdf_report(
    chat: str,
    records: list[PdfFileRecord],
    *,
    exclude_byte_identical_details: bool = False,
) -> dict[str, Any]:
    ordered = sorted(records, key=_record_sort_key)
    by_name: dict[str, list[PdfFileRecord]] = {}
    by_hash: dict[str, list[PdfFileRecord]] = {}
    for record in ordered:
        by_name.setdefault(record.normalized_name, []).append(record)
        if record.sha256:
            by_hash.setdefault(record.sha256, []).append(record)

    name_groups = [
        {
            "normalized_name": key,
            "book_title": items[0].book_title,
            "possible_duplicates": len(items) > 1,
            "files": [
                {
                    "message_id": item.message_id,
                    "file_name": item.file_name,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "local_path": item.local_path,
                }
                for item in items
            ],
        }
        for key, items in sorted(by_name.items())
    ]
    canonical_by_hash = {
        digest: min(items, key=lambda item: item.message_id).message_id
        for digest, items in by_hash.items()
    }
    content_groups = []
    for digest, items in sorted(by_hash.items()):
        if len(items) <= 1:
            continue
        ordered_items = sorted(items, key=lambda item: item.message_id)
        canonical_id = ordered_items[0].message_id
        content_groups.append(
            {
                "sha256": digest,
                "canonical_message_id": canonical_id,
                "duplicate_message_ids": [item.message_id for item in ordered_items[1:]],
                "message_ids": [item.message_id for item in ordered_items],
                "file_names": [item.file_name for item in ordered_items],
            }
        )

    report_files: list[dict[str, Any]] = []
    detailed_message_ids: list[int] = []
    excluded_message_ids: list[int] = []
    for record in ordered:
        item = asdict(record)
        matching_hash_files = by_hash.get(record.sha256 or "", [])
        canonical_id = canonical_by_hash.get(record.sha256 or "")
        if record.sha256 and len(matching_hash_files) > 1:
            is_copy = record.message_id != canonical_id
            item["content_category"] = "byte_identical_copy" if is_copy else "canonical_byte_identical_copy"
            item["content_group_sha256"] = record.sha256
            item["identical_to_message_id"] = canonical_id if is_copy else None
        elif record.sha256:
            item["content_category"] = "verified_unique_content"
            item["content_group_sha256"] = None
            item["identical_to_message_id"] = None
        else:
            item["content_category"] = "not_compared"
            item["content_group_sha256"] = None
            item["identical_to_message_id"] = None

        is_identical_copy = item["content_category"] == "byte_identical_copy"
        if exclude_byte_identical_details and is_identical_copy:
            excluded_message_ids.append(record.message_id)
        else:
            detailed_message_ids.append(record.message_id)
        report_files.append(item)

    total_known_size = sum(item.size_bytes or 0 for item in ordered)
    return {
        "report_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_chat": chat,
        "summary": {
            "total_pdf_files": len(ordered),
            "unique_normalized_names": len(by_name),
            "possible_duplicate_name_groups": sum(len(items) > 1 for items in by_name.values()),
            "identical_content_groups": len(content_groups),
            "byte_identical_copies": sum(len(items) - 1 for items in by_hash.values() if len(items) > 1),
            "detailed_comparison_files": len(detailed_message_ids),
            "excluded_byte_identical_details": len(excluded_message_ids),
            "total_known_size_bytes": total_known_size,
            "files_with_captions": sum(bool(item.caption.strip()) for item in ordered),
            "downloaded_files": sum(item.local_path is not None for item in ordered),
            "files_with_pdf_metadata": sum(item.page_count is not None for item in ordered),
        },
        "name_groups": name_groups,
        "identical_content_groups": content_groups,
        "exclude_byte_identical_details": exclude_byte_identical_details,
        "detailed_comparison_message_ids": detailed_message_ids,
        "excluded_identical_message_ids": excluded_message_ids,
        "files": report_files,
    }


def _markdown_escape(value: Any) -> str:
    text = str(value)
    for character in ("\\", "`", "*", "_", "[", "]", "|", "<", ">"):
        text = text.replace(character, f"\\{character}")
    return text.replace("\r", "")


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# PDF channel report: {_markdown_escape(report['source_chat'])}",
        "",
        f"- Generated: {report['generated_at']}",
        f"- PDF files: {summary['total_pdf_files']}",
        f"- Unique normalized names: {summary['unique_normalized_names']}",
        f"- Possible duplicate-name groups: {summary['possible_duplicate_name_groups']}",
        f"- Identical-content groups (verified by SHA-256): {summary['identical_content_groups']}",
        f"- Byte-identical copies: {summary['byte_identical_copies']}",
        f"- Files with detailed entries: {summary['detailed_comparison_files']}",
        f"- Byte-identical copies omitted from details: {summary['excluded_byte_identical_details']}",
        f"- Known total size: {summary['total_known_size_bytes']} bytes",
        f"- Files with captions: {summary['files_with_captions']}",
        f"- Downloaded and inspected: {summary['downloaded_files']}",
        "",
        "> Name grouping is a filename-based candidate only. Identical-content groups are confirmed only for downloaded files with matching SHA-256 hashes.",
        "> When byte-identical details are filtered, the lowest Telegram message ID is retained as the representative; files without a verified hash remain in the detailed list.",
        "",
    ]
    if not report["name_groups"]:
        lines.append("No PDF attachments were found in the scanned message history.")
        return "\n".join(lines) + "\n"

    if report["identical_content_groups"]:
        lines.extend(["## Byte-identical content groups", ""])
        for content_group in report["identical_content_groups"]:
            lines.append(f"### SHA-256 `{content_group['sha256']}`")
            lines.append("")
            lines.append(f"- Canonical copy: message {content_group['canonical_message_id']}")
            lines.append(
                "- Byte-identical copies: "
                + ", ".join(str(message_id) for message_id in content_group["duplicate_message_ids"])
            )
            lines.append(
                "- Files: "
                + ", ".join(f"`{_markdown_escape(file_name)}`" for file_name in content_group["file_names"])
            )
            lines.append("")

    files_by_name: dict[str, list[dict[str, Any]]] = {}
    for record in report["files"]:
        files_by_name.setdefault(record["normalized_name"], []).append(record)
    detailed_ids = set(report.get("detailed_comparison_message_ids", [item["message_id"] for item in report["files"]]))

    for group in report["name_groups"]:
        files = group["files"]
        heading = files[0]["file_name"]
        lines.extend([f"## {_markdown_escape(heading)}", ""])
        if group["possible_duplicates"]:
            lines.append(f"**Possible duplicate group:** {len(files)} files with the same normalized name. Compare file sizes and hashes where available.")
            lines.append("")
        group_records = files_by_name.get(group["normalized_name"], [])
        detailed_records = [record for record in group_records if record["message_id"] in detailed_ids]
        omitted_count = len(group_records) - len(detailed_records)
        if omitted_count:
            lines.append(
                f"> {omitted_count} verified byte-identical copy/copies omitted from per-file details; "
                "see the SHA-256 groups above."
            )
            lines.append("")
        for record in detailed_records:
            lines.append(f"### {_markdown_escape(record['book_title'])}")
            lines.append("")
            lines.append(f"- File: `{_markdown_escape(record['file_name'])}`")
            lines.append(f"- Message ID: {record['message_id']}")
            lines.append(f"- Content category: `{record['content_category']}`")
            lines.append(f"- Date: {_markdown_escape(record['message_date'] or 'unknown')}")
            lines.append(f"- Title source: {_markdown_escape(record['title_source'])}")
            lines.append(f"- Size: {record['size_bytes'] if record['size_bytes'] is not None else 'unknown'} bytes")
            lines.append(f"- MIME type: {_markdown_escape(record['mime_type'] or 'unknown')}")
            if record["document_id"]:
                lines.append(f"- Telegram document ID: `{_markdown_escape(record['document_id'])}`")
            if record["pdf_title"]:
                lines.append(f"- Embedded PDF title: {_markdown_escape(record['pdf_title'])}")
            if record["pdf_author"]:
                lines.append(f"- PDF author: {_markdown_escape(record['pdf_author'])}")
            if record["pdf_subject"]:
                lines.append(f"- PDF subject: {_markdown_escape(record['pdf_subject'])}")
            if record["page_count"] is not None:
                lines.append(f"- Pages: {record['page_count']}")
            if record["sha256"]:
                lines.append(f"- SHA-256: `{record['sha256']}`")
            if record["local_path"]:
                lines.append(f"- Included PDF: `{_markdown_escape(record['local_path'])}`")
            if record["analysis_error"]:
                lines.append(f"- PDF inspection/download note: {_markdown_escape(record['analysis_error'])}")
            lines.append("- Caption:")
            if record["caption"].strip():
                lines.extend(f"  > {_markdown_escape(line)}" for line in record["caption"].splitlines())
            else:
                lines.append("  > *(none)*")
            lines.append("")

    return "\n".join(lines) + "\n"


def write_pdf_report(
    report: dict[str, Any],
    output_dir: str | Path,
    basename: str,
    *,
    report_format: str = "both",
) -> list[Path]:
    if report_format not in {"json", "markdown", "both"}:
        raise ValueError("Report format must be 'json', 'markdown', or 'both'.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", basename) or basename in {".", ".."}:
        raise ValueError("Report basename may contain only letters, numbers, dots, underscores, and hyphens.")

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    contents: list[tuple[str, str]] = []
    if report_format in {"json", "both"}:
        contents.append((".json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"))
    if report_format in {"markdown", "both"}:
        contents.append((".md", render_markdown_report(report)))

    for suffix, content in contents:
        path = destination / f"{basename}{suffix}"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        outputs.append(path)
    return outputs
