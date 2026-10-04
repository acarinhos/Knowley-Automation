"""Repair inaccessible 1.1.0 visual image URLs after explicit approval.

The default mode is read-only. This script fixes URL access for the remaining
visual questions. Source-image accuracy still needs editorial review. The known
incorrect Michael Jackson question is excluded from URL repair and deactivated.
"""

import argparse
import sys
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database.db_manager import get_storage_bucket, init_firebase  # noqa: E402
from firebase_admin import firestore  # noqa: E402


KNOWN_WRONG_IMAGE_IDS = {
    "013bb5287fe6d812555277e6da6484f274983f791c58cd996be30488c8c047d6",
}


def image_path(url: str, bucket_name: str) -> str | None:
    parsed = urlparse(url)
    prefix = f"/{bucket_name}/questions/images/"
    if parsed.scheme != "https" or parsed.netloc != "storage.googleapis.com":
        return None
    path = unquote(parsed.path)
    return path[len(bucket_name) + 2:] if path.startswith(prefix) else None


def download_url(bucket_name: str, path: str, token: str) -> str:
    return (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/"
        f"{quote(path, safe='')}?alt=media&token={token}"
    )


def repair(*, apply: bool = False) -> dict[str, int]:
    db = init_firebase()
    bucket = get_storage_bucket()
    candidates = []
    counts = {"visual": 0, "eligible": 0, "known_wrong_image": 0, "updated": 0, "deactivated": 0}
    wrong_image_snapshots = []
    for snap in db.collection("questions").stream():
        data = snap.to_dict() or {}
        if str(data.get("dbVersion") or data.get("version") or "") != "1.1.0":
            continue
        if data.get("is_visual_question") is not True:
            continue
        counts["visual"] += 1
        if snap.id in KNOWN_WRONG_IMAGE_IDS:
            counts["known_wrong_image"] += 1
            wrong_image_snapshots.append(snap)
            continue
        path = image_path(str(data.get("question_image_url") or ""), bucket.name)
        if path and isinstance(data.get("payload"), dict):
            candidates.append((snap, path))
    counts["eligible"] = len(candidates)
    if not apply:
        return counts

    token_cache = {}
    for snap, path in candidates:
        if path not in token_cache:
            blob = bucket.blob(path)
            blob.reload()
            metadata = dict(blob.metadata or {})
            if not metadata.get("source") or not metadata.get("license"):
                raise ValueError(f"Görselin kaynak/lisans meta verisi eksik: {snap.id}")
            token = str(metadata.get("firebaseStorageDownloadTokens") or "").split(",")[0].strip()
            if not token:
                token = str(uuid.uuid4())
                metadata["firebaseStorageDownloadTokens"] = token
                blob.metadata = metadata
                blob.patch(if_metageneration_match=blob.metageneration)
            token_cache[path] = token
        url = download_url(bucket.name, path, token_cache[path])
        response = requests.get(url, stream=True, timeout=20)
        try:
            if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
                raise ValueError(f"İndirilebilir görsel doğrulanamadı: {snap.id}, HTTP {response.status_code}")
        finally:
            response.close()
        snap.reference.update(
            {"question_image_url": url, "payload.question_image_url": url},
            option=firestore.LastUpdateOption(snap.update_time),
        )
        counts["updated"] += 1
    for snap in wrong_image_snapshots:
        snap.reference.update(
            {"is_active": False, "status": "needs_review"},
            option=firestore.LastUpdateOption(snap.update_time),
        )
        counts["deactivated"] += 1
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Update Storage token metadata and Firestore URLs")
    args = parser.parse_args()
    print(repair(apply=args.apply))
