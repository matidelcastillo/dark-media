import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "automation" / "manifests" / "es.json"
CHANNEL_ID = "6a9c5381065799be4695087e"
ORGANIZATION_ID = "6a9c53310052b63a90b7021b"
MEDIA_PREFIX = "https://raw.githubusercontent.com/matidelcastillo/dark-media/"
TIMEZONE = ZoneInfo("America/Mexico_City")
MAX_QUEUE = 10
MAX_CREATE_PER_RUN = 3
SLOTS = ((1, time(14)), (3, time(14)), (6, time(19)))


def valid_caption(caption):
    words = caption.strip().split()
    return bool(words) and len(words) <= 150 and not re.search(r"[#👇]", caption) and not re.search(r"\b(guardá|contanos|comentá)\b", caption, re.I)


def valid_cadence(value):
    due = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TIMEZONE)
    return (due.weekday(), due.time().replace(tzinfo=None, second=0, microsecond=0)) in SLOTS


def next_slot(now, occupied):
    start = now.astimezone(TIMEZONE) + timedelta(minutes=5)
    occupied = {datetime.fromisoformat(item.replace("Z", "+00:00")).astimezone(timezone.utc) for item in occupied}
    for offset in range(60):
        day = start.date() + timedelta(days=offset)
        for weekday, slot_time in SLOTS:
            if day.weekday() != weekday:
                continue
            local = datetime.combine(day, slot_time, TIMEZONE)
            candidate = local.astimezone(timezone.utc)
            if candidate >= start.astimezone(timezone.utc) and candidate not in occupied:
                return candidate.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    raise RuntimeError("No free cadence slot in the next 60 days")


def request_buffer(query, variables=None):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing GitHub Actions secret BUFFER_API_KEY")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request("https://api.buffer.com", data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Buffer HTTP {error.code}") from None
    if body.get("errors"):
        raise RuntimeError("; ".join(item.get("message", "Buffer API error") for item in body["errors"]))
    return body["data"]


def load_manifest():
    data = json.loads(MANIFEST.read_text())
    posts = data.get("posts")
    if data.get("channelId") != CHANNEL_ID or not isinstance(posts, list):
        raise RuntimeError("Invalid ES manifest or channel")
    slugs = set()
    for post in posts:
        slug = post.get("slug", "")
        assets = post.get("assets")
        approved = post.get("approvedAt", "")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug in slugs:
            raise RuntimeError(f"Invalid or duplicate slug: {slug or 'unknown'}")
        if not valid_caption(post.get("caption", "")):
            raise RuntimeError(f"Caption failed policy: {slug}")
        if not isinstance(assets, list) or len(assets) != 5 or len(set(assets)) != 5:
            raise RuntimeError(f"Expected five unique images: {slug}")
        if any(not isinstance(asset, str) or not asset.startswith(MEDIA_PREFIX) or f"/{slug}/" not in asset or not asset.endswith("_es.jpg") for asset in assets):
            raise RuntimeError(f"Non-public ES media URL: {slug}")
        try:
            approved_at = datetime.fromisoformat(approved.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise RuntimeError(f"Invalid approval date: {slug}") from None
        if approved_at.tzinfo is None or approved_at > datetime.now(timezone.utc):
            raise RuntimeError(f"Approval date must be timezone-aware and not future: {slug}")
        if post.get("dueAt") and (not valid_cadence(post["dueAt"]) or datetime.fromisoformat(post["dueAt"].replace("Z", "+00:00")) <= datetime.now(timezone.utc) + timedelta(minutes=5)):
            raise RuntimeError(f"Invalid or past dueAt: {slug}")
        slugs.add(slug)
    return data


def verify_asset(url):
    request = urllib.request.Request(url, headers={"Range": "bytes=0-1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status not in (200, 206) or not response.headers.get("Content-Type", "").startswith("image/jpeg") or response.read(2) != b"\xff\xd8":
                raise RuntimeError(f"Public image failed JPEG check: {url}")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Media HTTP {error.code}: {url}") from None


def main():
    manifest = load_manifest()
    if not manifest["posts"]:
        print("No approved ES posts; nothing queued.")
        return
    query = """query Posts($organizationId: OrganizationId!, $channelId: ChannelId!) {
      posts(first: 100, input: { organizationId: $organizationId, filter: { status: [scheduled, sent], channelIds: [$channelId] } }) {
        edges { node { id dueAt status assets { source } } }
      }
    }"""
    data = request_buffer(query, {"organizationId": ORGANIZATION_ID, "channelId": CHANNEL_ID})
    posts = [edge["node"] for edge in data["posts"]["edges"]]
    scheduled = [post for post in posts if post["status"] == "scheduled"]
    known_slugs = {match.group(1) for post in posts for asset in post.get("assets", []) if (match := re.search(r"/([a-z0-9]+(?:-[a-z0-9]+)*)/s[1-5]_es\.jpg(?:\?|$)", asset.get("source", "")))}
    candidates = [post for post in manifest["posts"] if post["slug"] not in known_slugs]
    available = min(MAX_CREATE_PER_RUN, MAX_QUEUE - len(scheduled))
    if available <= 0:
        print(f"Queue full: {len(scheduled)}/{MAX_QUEUE}; nothing queued.")
        return
    created = []
    occupied = {post["dueAt"] for post in scheduled if post.get("dueAt")}
    for post in candidates[:available]:
        for asset in post["assets"]:
            verify_asset(asset)
        due_at = post.get("dueAt") or next_slot(datetime.now(timezone.utc), occupied)
        if due_at in occupied:
            raise RuntimeError(f"Buffer slot already occupied: {due_at}")
        mutation = """mutation CreatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id status dueAt assets { source } } }
            ... on MutationError { message }
          }
        }"""
        result = request_buffer(mutation, {"input": {
            "channelId": CHANNEL_ID,
            "text": post["caption"],
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": due_at,
            "assets": [{"image": {"url": url}} for url in post["assets"]],
            "metadata": {"instagram": {"type": "post", "shouldShareToFeed": True, "isAiGenerated": True}},
            "aiAssisted": True,
            "source": "dark-github-actions",
        }})
        created_post = result["createPost"].get("post")
        if not created_post or created_post.get("status") != "scheduled" or len(created_post.get("assets", [])) != 5:
            raise RuntimeError(result["createPost"].get("message", f"Buffer rejected {post['slug']}"))
        created.append({"slug": post["slug"], "id": created_post["id"], "dueAt": created_post["dueAt"]})
        occupied.add(due_at)
    print(json.dumps({"scheduled_before": len(scheduled), "created": created}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
