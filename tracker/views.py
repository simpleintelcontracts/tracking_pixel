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

from django.shortcuts import render
from django.db.models import Count
from datetime import date, timedelta
from .models import Event, Lead, Session

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

        # Fallback for plain Django POST
        if not data and "p" in request.POST:
            try:
                data = json.loads(request.POST["p"])
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

def dashboard_view(request):
    today = date.today()
    two_weeks_ago = today - timedelta(days=14)

    site_keys = Event.objects.values_list('site_key', flat=True).distinct()
    active_site_key = request.GET.get('site_key', '')

    # Filtering
    events_qs = Event.objects.all()
    leads_qs = Lead.objects.all()

    if active_site_key:
        events_qs = events_qs.filter(site_key=active_site_key)
        leads_qs = leads_qs.filter(event__site_key=active_site_key)

    from_date_str = request.GET.get('from', two_weeks_ago.isoformat())
    to_date_str = request.GET.get('to', today.isoformat())

    from_date = date.fromisoformat(from_date_str)
    to_date = date.fromisoformat(to_date_str)

    events_qs = events_qs.filter(created_at__date__range=[from_date, to_date])
    leads_qs = leads_qs.filter(created_at__date__range=[from_date, to_date])

    # KPIs
    total_events = events_qs.count()
    page_loads = events_qs.filter(event_type=Event.EVENT_PAGE_LOAD).count()
    form_submits = events_qs.filter(event_type=Event.EVENT_FORM_SUBMIT).count()
    unique_visitors = events_qs.values('session__client_id').distinct().count()

    # Events over time (Chart.js data)
    daily_events = events_qs.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    daily_events = daily_events.values('created_at__date').annotate(count=Count('id')).order_by('created_at__date')

    days = []
    counts = []
    current_day = from_date
    while current_day <= to_date:
        days.append(current_day.isoformat())
        count_for_day = next((item['count'] for item in daily_events if item['created_at__date'] == current_day), 0)
        counts.append(count_for_day)
        current_day += timedelta(days=1)

    # Top Pages
    top_pages = events_qs.filter(url__isnull=False).values('url').annotate(events=Count('id')).order_by('-events')[:10]

    # Top Campaigns
    top_campaigns = events_qs.filter(utm_source__isnull=False).values('utm_source', 'utm_campaign').annotate(events=Count('id')).order_by('-events')[:10]

    # Recent Leads
    recent_leads = leads_qs.order_by('-created_at')[:10]

    context = {
        'site_keys': site_keys,
        'active_site_key': active_site_key,
        'from': from_date_str,
        'to': to_date_str,
        'total_events': total_events,
        'page_loads': page_loads,
        'form_submits': form_submits,
        'unique_visitors': unique_visitors,
        'days': days,
        'counts': counts,
        'top_pages': top_pages,
        'top_campaigns': top_campaigns,
        'new_leads': leads_qs.count(),
        'recent_leads': recent_leads,
    }
    return render(request, 'dashboard.html', context)
