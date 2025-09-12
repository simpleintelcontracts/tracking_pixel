from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from .models import Event, Lead, Session

def dashboard(request):
    # --- Filters ---
    site_key = request.GET.get("site_key")
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    now = timezone.now()
    default_from = (now - timedelta(days=14)).date()
    default_to = now.date()

    def _parse(d, default):
        try:
            return timezone.datetime.fromisoformat(d).date()
        except Exception:
            return default

    start_date = _parse(date_from, default_from)
    end_date = _parse(date_to, default_to)

    event_qs = Event.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    lead_qs = Lead.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    session_qs = Session.objects.filter(
        first_seen__date__gte=start_date,
        first_seen__date__lte=end_date
    )

    if site_key:
        event_qs = event_qs.filter(site_key=site_key)
        session_qs = session_qs.filter(site_key=site_key)

    # KPIs
    total_events = event_qs.count()
    page_loads = event_qs.filter(event_type="page_load").count()
    form_submits = event_qs.filter(event_type="form_submission").count()
    unique_visitors = (
        session_qs.values("client_id")
        .exclude(client_id__isnull=True)
        .exclude(client_id="")
        .distinct()
        .count()
    )
    new_leads = lead_qs.count()

    # Time series
    daily = (
        event_qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    days = [d["day"].isoformat() for d in daily]
    counts = [d["count"] for d in daily]

    # Top pages
    top_pages = (
        event_qs.exclude(url__isnull=True)
        .values("url")
        .annotate(events=Count("id"))
        .order_by("-events")[:10]
    )

    # Top campaigns
    top_campaigns = (
        event_qs.values("utm_source", "utm_campaign")
        .annotate(events=Count("id"))
        .order_by("-events")[:10]
    )

    # Recent leads
    recent_leads = (
        lead_qs.order_by("-created_at")[:10]
        .values("first_name", "last_name", "email", "phone", "property_address", "created_at")
    )

    site_keys = (
        Event.objects.exclude(site_key__isnull=True)
        .values_list("site_key", flat=True)
        .distinct()
        .order_by("site_key")
    )

    ctx = {
        "site_keys": site_keys,
        "active_site_key": site_key or "",
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "total_events": total_events,
        "page_loads": page_loads,
        "form_submits": form_submits,
        "unique_visitors": unique_visitors,
        "new_leads": new_leads,
        "days": days,
        "counts": counts,
        "top_pages": top_pages,
        "top_campaigns": top_campaigns,
        "recent_leads": recent_leads,
    }
    return render(request, "tracker/dashboard.html", ctx)
