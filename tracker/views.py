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
import json
import csv

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

    def post(self, request, *args, **kwargs):
        # Support both JSON body and form-encoded "p=<json>"
        data = request.data

        # If DRF already parsed form data, p will be present in request.data
        if isinstance(data, dict) and "p" in data and isinstance(data["p"], str):
            try:
                data = json.loads(data["p"])
            except Exception:
                return Response({"error": "Invalid JSON in 'p'."}, status=status.HTTP_400_BAD_REQUEST)

        # Accept single event or array of events
        events = data if isinstance(data, list) else [data]

        created_pks = []
        for payload in events:
            ser = EventSerializer(data=payload, context={"request": request})
            if ser.is_valid():
                event = ser.save()
                created_pks.append(event.pk)
            else:
                # helpful when debugging
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        for pk in created_pks:
            process_event_data(pk)

        return Response(status=status.HTTP_204_NO_CONTENT)

def collect_gif_view(request):
    # 1x1 transparent GIF
    GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,"
           b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")

    data = request.GET.dict()
    data.setdefault("event_type", "page_load")
    data.setdefault("v", 1)
    data.setdefault("site_key", "noscript_fallback")
    data["session_id"] = generate_simple_session_id()
    data["client_id"]  = generate_simple_client_id()
    data["event_id"]   = str(uuid.uuid4())

    ser = EventSerializer(data=data, context={"request": request})
    if ser.is_valid():
        event = ser.save()
        process_event_data.delay(event.pk)

    return HttpResponse(GIF, content_type="image/gif")
