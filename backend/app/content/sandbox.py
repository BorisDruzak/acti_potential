"""Run parsers with a bounded Linux subprocess and no application credentials."""

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from app.content.parsers import (
    MAX_BLOCKS,
    MAX_INPUT_BYTES,
    MAX_PAGES,
    MAX_TEXT_BYTES,
    empty_result,
    parser_identity,
)

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
BOOTSTRAP = """import resource,runpy,sys
resource.setrlimit(resource.RLIMIT_CPU,(20,21))
resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024))
resource.setrlimit(resource.RLIMIT_FSIZE,(25*1024*1024+1,25*1024*1024+1))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
sys.path.insert(0,sys.argv.pop(1))
runpy.run_module("app.content.parsers",run_name="__main__")
"""


def validate_result(result):
    if not isinstance(result, dict) or set(result) - {
        "state",
        "parser",
        "parser_version",
        "full_text",
        "blocks",
        "warnings",
        "error_code",
    }:
        raise ValueError("Invalid parser result")
    if result.get("state") not in {
        "completed",
        "no_text",
        "unsupported",
        "encrypted",
        "failed",
    } or result.get("parser") not in {"pdf", "docx", "doc", "unsupported"}:
        raise ValueError("Invalid parser state")
    if not isinstance(result.get("parser_version"), str) or len(result["parser_version"]) > 100:
        raise ValueError("Invalid parser version")
    text = result.get("full_text")
    blocks = result.get("blocks")
    warnings = result.get("warnings")
    if not isinstance(text, str) or "\0" in text or len(text.encode()) > MAX_TEXT_BYTES:
        raise ValueError("Invalid extracted text")
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise ValueError("Invalid parser blocks")
    if (
        not isinstance(warnings, list)
        or len(warnings) > 100
        or any(
            not isinstance(warning, str) or not re.fullmatch(r"[a-z_]{1,100}", warning)
            for warning in warnings
        )
    ):
        raise ValueError("Invalid parser warning")
    code = result.get("error_code")
    if code is not None and (not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,100}", code)):
        raise ValueError("Invalid parser error")
    text_size, anchors = 0, set()
    for index, block in enumerate(blocks):
        if (
            not isinstance(block, dict)
            or set(block) - {"kind", "ordinal", "parent_ordinal", "page", "anchor", "text", "table"}
            or block.get("kind")
            not in {"paragraph", "heading", "page", "table", "table_row", "point"}
        ):
            raise ValueError("Invalid block definition")
        anchor = block.get("anchor")
        if (
            not isinstance(anchor, str)
            or not re.fullmatch(r"[a-z][a-z0-9_:.-]{0,199}", anchor)
            or anchor in anchors
        ):
            raise ValueError("Invalid block anchor")
        anchors.add(anchor)
        if type(block.get("ordinal")) is not int or block["ordinal"] != index:
            raise ValueError("Invalid block order")
        parent = block.get("parent_ordinal")
        if parent is not None and (type(parent) is not int or not 0 <= parent < index):
            raise ValueError("Invalid parent block")
        page = block.get("page")
        if page is not None and (type(page) is not int or not 1 <= page <= MAX_PAGES):
            raise ValueError("Invalid page")
        if not isinstance(block.get("text"), str) or "\0" in block["text"]:
            raise ValueError("Invalid block text")
        text_size += len(block["text"].encode())
        if text_size > MAX_TEXT_BYTES:
            raise ValueError("Parser text limit")
        table = block.get("table")
        if table is not None and (
            not isinstance(table, list)
            or len(table) > MAX_BLOCKS
            or any(not isinstance(row, list) or len(row) > 1000 for row in table)
            or sum(len(row) for row in table) > 20000
            or any(not isinstance(cell, str) or "\0" in cell for row in table for cell in row)
        ):
            raise ValueError("Invalid table structure")
    if result["state"] in {"failed", "unsupported", "encrypted"} and (text or blocks):
        raise ValueError("Failed parsing cannot expose partial content")
    return result


def extract_file(storage_root, storage_key, name, mime, expected_sha256, *, timeout_seconds=30):
    """Read an immutable object securely; never give its path to a native parser."""
    from app.storage.local import LocalFileStorage

    identity = parser_identity(name, mime)
    if identity[0] == "unsupported":
        return empty_result(name, mime, "unsupported", "unsupported_format")
    if sys.platform != "linux":
        return empty_result(name, mime, "failed", "sandbox_unavailable")
    if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 60:
        raise ValueError("Invalid parser timeout")
    try:
        storage = LocalFileStorage(storage_root)
        with storage.open_stream(storage_key) as original:
            data = original.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            return empty_result(name, mime, "failed", "limit_exceeded")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            return empty_result(name, mime, "failed", "integrity_mismatch")
    except (OSError, ValueError):
        return empty_result(name, mime, "failed", "storage_unavailable")
    with tempfile.TemporaryDirectory(prefix="acti-content-") as directory:
        # The application credentials, DB URL and original object path are not inherited.
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": directory,
            "HOME": directory,
        }
        command = [
            sys.executable,
            "-I",
            "-c",
            BOOTSTRAP,
            str(Path(__file__).resolve().parents[2]),
            name,
            mime,
        ]
        with tempfile.TemporaryFile(dir=directory) as output:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    cwd=directory,
                    env=environment,
                    start_new_session=True,
                    close_fds=True,
                    shell=False,
                )
                try:
                    process.communicate(input=data, timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    return empty_result(name, mime, "failed", "timeout")
                if process.returncode:
                    code = "limit_exceeded" if process.returncode < 0 else "parser_failed"
                    return empty_result(name, mime, "failed", code)
                output.seek(0)
                encoded = output.read(MAX_OUTPUT_BYTES + 1)
                if len(encoded) > MAX_OUTPUT_BYTES:
                    return empty_result(name, mime, "failed", "limit_exceeded")
                result = validate_result(json.loads(encoded))
                if (result["parser"], result["parser_version"]) != identity:
                    return empty_result(name, mime, "failed", "parser_identity_mismatch")
                return result
            except (OSError, ValueError, UnicodeError):
                return empty_result(name, mime, "failed", "parser_failed")
