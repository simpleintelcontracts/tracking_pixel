from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle # Import AnonRateThrottle
from .serializers import EventSerializer
from .tasks import process_event_data
from django.http import HttpResponse
import uuid
import time


def generate_simple_session_id():
    return f"sid_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def generate_simple_client_id():
    return f"cid_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class CollectView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = EventSerializer(data=request.data)
        if serializer.is_valid():
            event = serializer.save()
            process_event_data(event.id)  # Trigger Celery task
            return Response({"message": "Event processed successfully!"}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def collect_gif_view(request):
    # Transparent 1x1 GIF
    GIF_DATA = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'

    # Extract data from query parameters
    data = request.GET.dict()
    data['event_type'] = data.get('event_type', 'page_load') # Default to page_load for GIF
    data['v'] = data.get('v', 0) # Default schema version for GIF fallback
    data['site_key'] = data.get('site_key', 'noscript_fallback') # Default site_key for GIF fallback

    # Generate simple IDs for GIF-based tracking
    data['session_id'] = generate_simple_session_id()
    data['client_id'] = generate_simple_client_id()
    data['event_id'] = uuid.uuid4()

    serializer = EventSerializer(data=data)
    if serializer.is_valid():
        event = serializer.save()
        process_event_data.delay(event.id)

    return HttpResponse(GIF_DATA, content_type='image/gif')
