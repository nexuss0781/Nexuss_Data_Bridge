# Telegram File Bridge

A small Python CLI for moving files through Telegram using a normal MTProto user session. It can download a media file from a message, upload a local file to a chat, or transfer a file from one Telegram chat to another by downloading it locally and re-uploading it.

## Install

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
tgbridge init
```

Edit `.env` and set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org). Then sign in once:

```bash
tgbridge login
```

Telegram will prompt for the phone number, login code, and (if enabled) 2-step verification password. When `TELEGRAM_SESSION` is blank, Telethon stores the authorized session locally at `SESSION_FILE` (the default is `.telegram-bridge-session.session`). If you already have a Telethon StringSession, you can put it in `TELEGRAM_SESSION` instead. **A session grants access to the Telegram account; never commit or share `.env` or session files.** The project ignores both and restricts their permissions where supported.

You can invoke the tool without installing the console script using `python -m telegram_file_bridge`.

## Use

Download the media on a known Telegram message:

```bash
tgbridge download --chat @source_channel --message-id 12345 --out ./downloads
```

Upload a file:

```bash
tgbridge upload --chat @destination_channel --file ./downloads/corpus.tar --caption "Amharic corpus batch 01"
```

Copy media from one chat to another (the source caption is kept unless `--caption` is supplied):

```bash
tgbridge transfer --from-chat @source_channel --message-id 12345 --to-chat @destination_channel
```

`--chat` accepts a username or a numeric Telegram peer ID that the signed-in account can access. The account must have access to the source and permission to post in the destination. Results are printed as JSON, including the filename, byte count, and message ID. Downloads use a sanitized Telegram filename and avoid overwriting existing files by default; pass `--overwrite` to replace one.

### Channel PDF report

Scan the channel's full message history and write a grouped report of PDF attachments:

```bash
tgbridge report --chat @source_channel --out ./reports
```

By default the command creates both a JSON report (complete machine-readable records) and a Markdown report. It lists the PDF filename, likely book title, size, MIME type, Telegram message/document IDs, date, and the complete caption, with files sorted and grouped by normalized filename. This groups likely redundant names together, but matching names alone do not prove two PDFs contain identical bytes.

To include the actual PDFs in a sibling folder and inspect embedded PDF title/author/subject metadata, page counts, and exact content duplicates, opt in to downloading them:

```bash
tgbridge report --chat @source_channel --out ./reports --download-pdfs
```

Downloaded PDFs are stored beside the report in a channel-and-run-specific folder. SHA-256 groups confirm byte-identical files among the PDFs that were downloaded, and each record is categorized as a verified unique file, the canonical copy, another byte-identical copy, or not compared. Reports and downloaded files are written with owner-only permissions where supported. The command scans all accessible message history unless `--limit N` is supplied; use the limit for a smaller test run. If the PDF itself has no embedded title, the report uses its filename (or caption as a fallback), so that fallback is an identifier, not a claim that document text was semantically verified.

For a shorter detailed comparison, filter out verified copies while keeping them in the complete JSON inventory and in the compact SHA-256 duplicate groups:

```bash
tgbridge report --chat @source_channel --out ./reports --download-pdfs --exclude-byte-identical-details
```

This filter requires `--download-pdfs`. Within each identical-byte group, the lowest Telegram message ID is kept as the representative; files whose download/hash check fails remain in the detailed comparison and are marked as not compared.

Choose `--format json`, `--format markdown`, or `--format both` to control report outputs.

## Size and storage

The default upload guard is **2 GiB**. Set `MAX_UPLOAD_GB=4` only if the account and client are eligible for Telegram's 4 GiB uploads. This local guard does not grant an account entitlement or override Telegram-side restrictions. Downloads and transfers require local disk space; a transfer temporarily stores the file on disk and removes the temporary copy afterward.

## Verify

Run the deterministic end-to-end tests (they exercise download → upload, transfer, and the protected web sign-in/persistent-session flow with fake clients; they do not require account credentials or send anything to Telegram):

```bash
python -m unittest discover -s tests -v
```

A separate **live Telegram** check was completed for this build: a small sample file was uploaded and downloaded, and the bytes matched exactly. This real-session check is separate from the automated tests; running the test suite does not contact Telegram.

## Notes

- The file bridge is a local CLI. Its small sign-in page is gated by a one-time access code and is intended to be opened over the provided HTTPS address; it does not expose an unauthenticated upload endpoint.
- The implementation uploads files as documents to avoid Telegram converting supported media into a photo/video representation.
- It uses Telethon (MTProto) and does not use a Telegram bot token.


## Browser sign-in

Start the small web page from the project directory:

```bash
python3 webapp.py
```

Open the provided HTTPS app address, enter the one-time app access code printed by the server, then enter your Telegram phone number and the code Telegram sends. If Telegram requests 2-step verification, enter that password in the page. The access code gates the page; the server does not log phone numbers, login codes, or passwords.

The page uses the same local Telethon SQLite session as the CLI (`SESSION_FILE` plus `.session`), restricts its file permissions where supported, and reconnects from that file after sign-in to verify the authorization survived a client restart. Closing/restarting the web app does not erase the session. The browser page only handles sign-in; use the existing `tgbridge upload`, `download`, or `transfer` commands for file operations.
