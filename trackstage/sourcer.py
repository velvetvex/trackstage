"""sourcer.py — Soulseek sourcing via slskd REST API.

Talks to the slskd daemon (localhost:5030) directly. Ranks search results
by format policy and availability, downloads to the DJ Inbox.
"""

import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

AUDIO_EXTS = {"flac", "wav", "aiff", "aif", "mp3", "m4a"}
LOSSLESS_EXTS = {"flac", "wav", "aiff", "aif"}
MIN_MP3_BITRATE = 320


@dataclass
class Candidate:
    username: str
    filename: str
    size: int
    bitrate: int | None
    extension: str
    free_upload_slots: bool
    queue_length: int


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _find_downloaded(inbox: Path, name: str) -> Path | None:
    """Locate a completed download by basename anywhere under the inbox.

    slskd nests downloads under the sender's remote folder structure, so the
    file is rarely at the flat inbox root. Matches on exact basename (not a
    glob — track names contain [] () & which are glob metacharacters).
    """
    if not inbox.exists():
        return None
    for p in inbox.rglob("*"):
        if p.name == name and p.is_file():
            return p
    return None


def rank_candidates(files: list[dict], fmt: str) -> list[Candidate]:
    """Filter by format policy and sort best-first.

    fmt='flac': lossless preferred; MP3 kept only if bitrate>=320.
    fmt='any':  any audio file kept.
    Sort key: lossless first, then higher bitrate, then free slot,
    then shorter queue, then larger file.
    """
    cands: list[Candidate] = []
    for f in files:
        ext = _ext(f.get("filename", ""))
        if ext not in AUDIO_EXTS:
            continue
        bitrate = f.get("bitRate")
        is_lossless = ext in LOSSLESS_EXTS
        if fmt == "flac" and not is_lossless:
            if bitrate is None or bitrate < MIN_MP3_BITRATE:
                continue
        cands.append(Candidate(
            username=f.get("username", ""),
            filename=f.get("filename", ""),
            size=int(f.get("size", 0)),
            bitrate=bitrate,
            extension=ext,
            free_upload_slots=bool(f.get("freeUploadSlots", False)),
            queue_length=int(f.get("queueLength", 0)),
        ))

    def sort_key(c: Candidate):
        return (
            0 if c.extension in LOSSLESS_EXTS else 1,   # lossless first
            -(c.bitrate or 0),                          # higher bitrate first
            0 if c.free_upload_slots else 1,            # free slot first
            c.queue_length,                             # shorter queue first
            -c.size,                                    # larger file first
        )

    return sorted(cands, key=sort_key)


class SlskdClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or os.environ.get("SLSKD_URL",
                         "http://localhost:5030")).rstrip("/")
        self.api_key = api_key or os.environ.get("SLSKD_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key})

    def search(self, query: str, timeout: float = 30.0) -> list[dict]:
        """Start a search, poll to completion, return flattened file dicts."""
        r = self.session.post(f"{self.base_url}/api/v0/searches",
                              json={"searchText": query}, timeout=15)
        r.raise_for_status()
        search_id = r.json()["id"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.session.get(
                f"{self.base_url}/api/v0/searches/{search_id}", timeout=15).json()
            if s.get("state", "").startswith("Completed") or s.get("isComplete"):
                break
            time.sleep(1.0)

        resp = self.session.get(
            f"{self.base_url}/api/v0/searches/{search_id}/responses",
            timeout=15).json()
        files = []
        for r_ in resp:
            uname = r_.get("username", "")
            slots = r_.get("hasFreeUploadSlot", False)
            qlen = r_.get("queueLength", 0)
            for fdict in r_.get("files", []):
                files.append({
                    "username": uname,
                    "filename": fdict.get("filename", ""),
                    "size": fdict.get("size", 0),
                    "bitRate": fdict.get("bitRate"),
                    "freeUploadSlots": slots,
                    "queueLength": qlen,
                })
        return files

    def download(self, cand: Candidate, wait: bool = True,
                 timeout: float = 600.0) -> Path:
        """Enqueue a download; wait for completion; return the Inbox file path."""
        payload = [{"filename": cand.filename, "size": cand.size}]
        r = self.session.post(
            f"{self.base_url}/api/v0/transfers/downloads/{cand.username}",
            json=payload, timeout=15)
        r.raise_for_status()

        inbox = Path(os.environ["INBOX_PATH"])
        target_name = cand.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not wait:
            return inbox / target_name

        # slskd preserves the remote folder structure, so the completed file
        # lands at inbox/<remote dirs...>/<name>, NOT flat inbox/<name>. Search
        # recursively for the basename rather than polling a fixed flat path.
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = _find_downloaded(inbox, target_name)
            if found is not None:
                return found
            time.sleep(2.0)
        raise TimeoutError(f"Download did not complete: {target_name}")
