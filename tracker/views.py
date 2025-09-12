# tracker/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.throttling import SimpleRateThrottle
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .serializers import EventSerializer
from .tasks import process_event_data
from django.http import HttpResponse
import uuid
import time

def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR"))

class TrackerRateThrottle(SimpleRateThrottle):
    scope = "tracker"
    def get_cache_key(self, request, view):
        ident = _client_ip(request) or self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}

def generate_simple_session_id():
    return f"sid_{int(time.time())}_{uuid.uuid4().hex[:8]}"

def generate_simple_client_id():
    return f"cid_{int(time.time())}_{uuid.uuid4().hex[:8]}"

@method_decorator(csrf_exempt, name="dispatch")
class CollectView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [TrackerRateThrottle]

    def post(self, request, *args, **kwargs):
        # Accept either a single object or an array of events (batch from sendBeacon)
        data = request.data
        events = data if isinstance(data, list) else [data]

        created_any = False
        ids = []
        for payload in events:
            ser = EventSerializer(data=payload, context={"request": request})
            if ser.is_valid():
                event = ser.save()
                created_any = True
                ids.append(event.pk)
            # If invalid, skip silently to keep beacon flow non-blocking

        # Kick off async post-processing (batch)
        for pk in ids:
            process_event_data.delay(pk)

        # 204 for beacons (no content)
        return Response(status=status.HTTP_204_NO_CONTENT if created_any else status.HTTP_400_BAD_REQUEST)

def collect_gif_view(request):
    # 1x1 transparent GIF
    GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,"
           b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")

    data = request.GET.dict()
    data.setdefault("event_type", "page_load")
    data.setdefault("v", 0)
    data.setdefault("site_key", "noscript_fallback")
    data["session_id"] = generate_simple_session_id()
    data["client_id"]  = generate_simple_client_id()
    data["event_id"]   = str(uuid.uuid4())

    ser = EventSerializer(data=data, context={"request": request})
    if ser.is_valid():
        event = ser.save()
        process_event_data.delay(event.pk)

    return HttpResponse(GIF, content_type="image/gif")
