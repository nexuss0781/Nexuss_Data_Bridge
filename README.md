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

By default the command creates both a JSON report (complete machine-readable records) and a Markdown report. It lists the PDF filename, likely book title, size, MIME type, Telegram message/document IDs, date, and the complete caption. Rows with a SHA-256-confirmed byte-identical match appear first; each priority section is then sorted by normalized filename. Matching names alone do not prove two PDFs contain identical bytes.

To include the actual PDFs in a sibling folder and inspect embedded PDF title/author/subject metadata, page counts, and exact content duplicates, opt in to downloading them:

```bash
tgbridge report --chat @source_channel --out ./reports --download-pdfs
```

Downloaded PDFs are stored beside the report in a channel-and-run-specific folder. SHA-256 groups confirm byte-identical files among the PDFs that were downloaded, and each record is categorized as verified unique, byte-identical, or not compared. The full report keeps every PDF in the name-sorted inventory, then repeats all verified byte-identical files in a separate final review section with their message IDs, filenames, captions, hashes, and available PDF metadata. This appendix is for human keep/eliminate review; it does not remove files or make that decision. Reports and downloaded files are written with owner-only permissions where supported. The command scans all accessible message history unless `--limit N` is supplied; use the limit for a smaller test run. If the PDF itself has no embedded title, the report uses its filename (or caption as a fallback), so that fallback is an identifier, not a claim that document text was semantically verified.

For a shorter name-sorted per-file listing, optionally suppress detailed entries for verified copies:

```bash
tgbridge report --chat @source_channel --out ./reports --download-pdfs --exclude-byte-identical-details
```

This filter requires `--download-pdfs`. It does not delete records: all files remain in the complete JSON inventory and the final byte-identical review section. The lowest Telegram message ID is only a reference entry, not a keep recommendation; files whose download/hash check fails remain in the main inventory and are marked as not compared.

Choose `--format json`, `--format markdown`, or `--format both` to control report outputs.
The Markdown inventory and final review section use compact tables. Captions remain in their table cells, with embedded line breaks shown as `↵`; the Markdown tables omit the content-category column, while JSON retains those classifications.
Both report formats include Boolean `Duplicate`, `Status`, and `Processed` fields. `Duplicate` is true only for records in a verified identical-byte set; false does not prove uniqueness when a PDF was not hashed. `Status` defaults to true (enabled for batch selection), and `Processed` defaults to false until plaintext conversion succeeds. Setting `Status` to false is the analyst's opt-out for a status-aware batch operation.

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
