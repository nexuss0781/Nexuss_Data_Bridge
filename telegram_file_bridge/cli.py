from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .core import download_message, transfer_message, upload_file
from .reports import build_pdf_report, download_pdf_files, scan_pdf_files, write_pdf_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgbridge",
        description="A local Telegram file bridge: download, upload, transfer, and report on channel PDF files.",
    )
    parser.add_argument("--env-file", default=".env", help="credentials/config file (default: .env)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="create a private .env configuration template")
    subparsers.add_parser("login", help="sign in once and store a local Telegram session")

    download = subparsers.add_parser("download", help="download a file attached to a Telegram message")
    download.add_argument("--chat", required=True, help="chat username, link-resolvable name, or numeric peer ID")
    download.add_argument("--message-id", required=True, type=int)
    download.add_argument("--out", default="downloads", help="output directory (default: ./downloads)")
    download.add_argument("--overwrite", action="store_true", help="replace an existing same-name file")

    upload = subparsers.add_parser("upload", help="upload a local file to a Telegram chat")
    upload.add_argument("--chat", required=True)
    upload.add_argument("--file", required=True, dest="file_path")
    upload.add_argument("--caption", default=None)

    transfer = subparsers.add_parser("transfer", help="download a message's file, then upload it to another chat")
    transfer.add_argument("--from-chat", required=True)
    transfer.add_argument("--message-id", required=True, type=int)
    transfer.add_argument("--to-chat", required=True)
    transfer.add_argument("--caption", default=None, help="override the source caption; defaults to the source caption")

    report = subparsers.add_parser("report", help="scan a channel's full history and report its PDF files")
    report.add_argument("--chat", required=True, help="channel username, link-resolvable name, or numeric peer ID")
    report.add_argument("--out", default="reports", help="report output directory (default: ./reports)")
    report.add_argument("--format", dest="report_format", choices=("json", "markdown", "both"), default="both")
    report.add_argument("--limit", type=int, default=None, help="scan only the newest N messages (default: all history)")
    report.add_argument(
        "--download-pdfs",
        action="store_true",
        help="also download each PDF, inspect its metadata/pages, and verify duplicate content by SHA-256",
    )
    return parser


def _create_client(config: Config) -> Any:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed. Run: python -m pip install -r requirements.txt") from exc

    session: Any = StringSession(config.session_string) if config.session_string else str(config.session_file)
    return TelegramClient(session, config.api_id, config.api_hash)


def _restrict_session_permissions(config: Config) -> None:
    """Protect the local session database or configured .env containing a session string."""
    try:
        if config.session_string:
            if config.env_file.exists():
                os.chmod(config.env_file, 0o600)
        else:
            session_path = config.session_file
            if not str(session_path).endswith(".session"):
                session_path = Path(f"{session_path}.session")
            if session_path.exists():
                os.chmod(session_path, 0o600)
    except OSError:
        # Permission hardening is best effort on filesystems that do not support POSIX modes.
        pass


def _init(env_file: str) -> int:
    destination = Path(env_file).expanduser().resolve()
    if destination.exists():
        print(f"Configuration already exists: {destination}")
        return 0
    template = Path(__file__).resolve().parent / "env.example"
    if not template.exists():
        raise FileNotFoundError(f"Configuration template is missing: {template}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    print(f"Created {destination}. Set TELEGRAM_API_ID and TELEGRAM_API_HASH, then run: tgbridge login")
    return 0


async def _run_with_client(args: argparse.Namespace, config: Config) -> int:
    client = _create_client(config)
    try:
        await client.start()
        _restrict_session_permissions(config)

        if args.command == "login":
            me = await client.get_me()
            username = getattr(me, "username", None)
            print(json.dumps({"logged_in": True, "user_id": getattr(me, "id", None), "username": username}, indent=2))
            return 0

        if args.command == "report":
            records = await scan_pdf_files(client, args.chat, limit=args.limit)
            output_dir = Path(args.out).expanduser().resolve()
            safe_chat = re.sub(r"[^A-Za-z0-9._-]+", "-", args.chat.lstrip("@").strip()).strip(".-_") or "channel"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            basename = f"{safe_chat}-pdf-report-{timestamp}"
            pdf_directory = None
            if args.download_pdfs and records:
                pdf_directory = output_dir / f"{basename}-pdfs"
                records = await download_pdf_files(
                    client,
                    args.chat,
                    records,
                    pdf_directory,
                    report_root=output_dir,
                )
            report_data = build_pdf_report(args.chat, records)
            report_paths = write_pdf_report(
                report_data,
                output_dir,
                basename,
                report_format=args.report_format,
            )
            print(
                json.dumps(
                    {
                        "action": "report",
                        "chat": args.chat,
                        "summary": report_data["summary"],
                        "report_files": [str(path) for path in report_paths],
                        "pdf_directory": str(pdf_directory) if pdf_directory else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "download":
            result = await download_message(
                client,
                args.chat,
                args.message_id,
                args.out,
                overwrite=args.overwrite,
            )
        elif args.command == "upload":
            result = await upload_file(
                client,
                args.chat,
                args.file_path,
                caption=args.caption,
                max_upload_bytes=config.max_upload_bytes,
            )
        elif args.command == "transfer":
            result = await transfer_message(
                client,
                args.from_chat,
                args.message_id,
                args.to_chat,
                caption=args.caption,
                max_upload_bytes=config.max_upload_bytes,
            )
        else:
            raise ValueError(f"Unsupported command: {args.command}")

        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.env_file)
        config = Config.load(args.env_file)
        return asyncio.run(_run_with_client(args, config))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
