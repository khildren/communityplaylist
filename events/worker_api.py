"""
Worker API — used by the Unraid pull-worker.
All endpoints require X-Worker-Secret header.
"""
import json
import os
import subprocess
from pathlib import Path
from functools import wraps
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils import timezone


ALLOWED_COMMANDS = {
    "import_feeds", "import_venue_feeds", "geocode_events",
    "generate_recurring_events", "enrich_artists_musicbrainz",
    "enrich_artists_spotify", "enrich_artists_beatport",
    "enrich_artists_discogs", "harvest_youtube_videos",
    "harvest_twitch", "harvest_instagram", "discover_instagram",
    "bluesky_digest", "daily_digest", "compress_images",
    "auto_stub_artists", "check_live_streams", "dedup_events",
    "fetch_event_images",
}


def _worker_auth(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        secret = getattr(settings, "WORKER_SECRET", "")
        if not secret or request.headers.get("X-Worker-Secret") != secret:
            return JsonResponse({"error": "forbidden"}, status=403)
        return view(request, *args, **kwargs)
    return inner


@_worker_auth
def pending_tasks(request):
    from .models import WorkerTask
    tasks = list(
        WorkerTask.objects.filter(status="queued")
        .values("id", "task_type", "payload")[:20]
    )
    return JsonResponse(tasks, safe=False)


@csrf_exempt
@_worker_auth
def claim_task(request, task_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from .models import WorkerTask
    updated = WorkerTask.objects.filter(id=task_id, status="queued").update(
        status="running"
    )
    if not updated:
        return JsonResponse({"error": "not available"}, status=409)
    return JsonResponse({"ok": True})


@csrf_exempt
@_worker_auth
def complete_task(request, task_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from .models import WorkerTask, Event, Venue
    body = json.loads(request.body)
    task = WorkerTask.objects.filter(id=task_id).first()
    if not task:
        return JsonResponse({"error": "not found"}, status=404)

    task.result = body
    task.status = "done"
    task.completed_at = timezone.now()
    task.save()

    # Apply results back to the originating model
    if task.task_type == "geocode_event":
        lat, lng = body.get("lat"), body.get("lng")
        if lat and lng:
            Event.objects.filter(id=task.payload["event_id"]).update(
                latitude=lat, longitude=lng
            )
    elif task.task_type == "geocode_venue":
        lat, lng = body.get("lat"), body.get("lng")
        if lat and lng:
            Venue.objects.filter(id=task.payload["venue_id"]).update(
                latitude=lat, longitude=lng
            )
    elif task.task_type == "enrich_flyer":
        # body = dict of extracted flyer fields — only fill blanks
        import re
        event = Event.objects.filter(id=task.payload["event_id"]).first()
        if event and body:
            changed = []
            if body.get("title") and not event.title:
                event.title = body["title"][:200]; changed.append("title")
            if body.get("description") and not event.description:
                event.description = body["description"]; changed.append("description")
            if body.get("venue_name") and not event.location:
                loc = body["venue_name"]
                if body.get("venue_address"):
                    loc = f"{loc}, {body['venue_address']}"
                event.location = loc[:300]; changed.append("location")
            if body.get("price") and not event.price_info:
                event.price_info = body["price"][:100]; changed.append("price_info")
                if re.search(r"\d", body["price"].lower()):
                    event.is_free = False; changed.append("is_free")
            if body.get("ticket_url") and not event.website:
                event.website = body["ticket_url"][:500]; changed.append("website")
            event.flyer_scanned = True
            fields = ["flyer_scanned"] + [f for f in changed if f in ["title", "description", "location", "price_info", "is_free", "website"]]
            event.save(update_fields=fields)

    return JsonResponse({"ok": True})


@csrf_exempt
@_worker_auth
def error_task(request, task_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from .models import WorkerTask
    body = json.loads(request.body)
    WorkerTask.objects.filter(id=task_id).update(
        status="error",
        error_msg=str(body.get("error", ""))[:500],
        completed_at=timezone.now(),
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@_worker_auth
def trigger_command(request, cmd_name):
    """
    Fire a management command as a non-blocking subprocess on Plesk.
    Unraid calls this on schedule so the heavy imports run here (DB access
    needed) but Unraid decides *when*, avoiding peak-hour load on Plesk.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    if cmd_name not in ALLOWED_COMMANDS:
        return JsonResponse({"error": "unknown command"}, status=400)

    lock_file = Path(settings.BASE_DIR) / f".worker_lock_{cmd_name}"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            os.kill(pid, 0)
            return JsonResponse({"error": "already running", "pid": pid}, status=409)
        except (OSError, ValueError):
            lock_file.unlink(missing_ok=True)

    venv_python = Path(settings.BASE_DIR) / "venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else "python3"
    manage_py = str(Path(settings.BASE_DIR) / "manage.py")

    proc = subprocess.Popen(
        [python, manage_py, cmd_name],
        cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    lock_file.write_text(str(proc.pid))
    return JsonResponse({"ok": True, "pid": proc.pid, "cmd": cmd_name})
