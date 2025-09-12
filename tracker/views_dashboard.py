from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone
from django.http import HttpResponse
from datetime import timedelta, datetime, date
import csv
import json

from .models import Event, Lead, Session
from django.db import models


def dashboard(request):
    today = date.today()
    two_weeks_ago = today - timedelta(days=14)

    site_keys = Event.objects.values_list('site_key', flat=True).distinct()
    active_site_key = request.GET.get('site_key', '')

    # Filtering base querysets
    events_base_qs = Event.objects.all()
    leads_base_qs = Lead.objects.all()
    sessions_base_qs = Session.objects.all()

    if active_site_key:
        events_base_qs = events_base_qs.filter(site_key=active_site_key)
        leads_base_qs = leads_base_qs.filter(event__site_key=active_site_key) # Leads linked via events
        sessions_base_qs = sessions_base_qs.filter(site_key=active_site_key)

    # Apply date range filter to all base querysets
    from_date_str = request.GET.get('from', two_weeks_ago.isoformat())
    to_date_str = request.GET.get('to', today.isoformat())

    from_date = date.fromisoformat(from_date_str)
    to_date = date.fromisoformat(to_date_str)

    events_qs = events_base_qs.filter(created_at__date__range=[from_date, to_date])
    leads_qs = leads_base_qs.filter(created_at__date__range=[from_date, to_date])
    sessions_qs = sessions_base_qs.filter(first_seen__date__range=[from_date, to_date])

    # Debugging print statement
    print(f"Dashboard: Active Site Key: {active_site_key}, Events count: {events_qs.count()}, Sessions count: {sessions_qs.count()}")

    # Handle CSV export
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="events_export.csv"'

        writer = csv.writer(response)
        writer.writerow(['Event ID', 'Event Type', 'Site Key', 'Session ID', 'Client ID', 'URL', 'Page Title', 'Referrer', 'UTM Source', 'UTM Campaign', 'Created At'])

        for event in events_qs.select_related('session'):
            writer.writerow([
                str(event.event_id),
                event.event_type,
                event.site_key,
                event.session.session_id if event.session else '',
                event.session.client_id if event.session else '',
                event.url or '',
                event.page_title or '',
                event.referrer or '',
                event.utm_source or '',
                event.utm_campaign or '',
                event.created_at.isoformat(),
            ])
        return response

    # KPIs
    total_events = events_qs.count()
    page_loads = events_qs.filter(event_type=Event.EVENT_PAGE_LOAD).count()
    form_submits = events_qs.filter(event_type=Event.EVENT_FORM_SUBMIT).count()
    unique_visitors = sessions_qs.values('client_id').distinct().count()

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

    # New KPIs
    identified_users = sessions_qs.filter(
        (models.Q(user_external_id__isnull=False) | models.Q(user_email__isnull=False)),
    ).distinct().count()
    # Assuming custom events for registration and login
    new_registrations = events_qs.filter(
        event_type=Event.EVENT_CUSTOM,
        event_data__event_name="user_registered"
    ).count()
    logins = events_qs.filter(
        event_type=Event.EVENT_CUSTOM,
        event_data__event_name="user_logged_in"
    ).count()

    # Recent Users Table
    recent_users = sessions_qs.filter(
        (models.Q(user_external_id__isnull=False) | models.Q(user_email__isnull=False)),
    ).order_by('-first_seen')[:10]

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
        'identified_users': identified_users,
        'new_registrations': new_registrations,
        'logins': logins,
        'recent_users': recent_users,
    }
    return render(request, 'dashboard.html', context)
