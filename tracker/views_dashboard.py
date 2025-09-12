from django.db import models
from django.db.models import Count, Q, Value, CharField, IntegerField
from django.db.models.functions import Coalesce, Cast
from django.shortcuts import render
from django.http import HttpResponse
from datetime import timedelta, date
import csv
import json

from .models import Event, Lead, Session


def dashboard(request):
    # --- Date range defaults (last 14 days) ---
    today = date.today()
    two_weeks_ago = today - timedelta(days=14)
    from_date_str = request.GET.get('from', two_weeks_ago.isoformat())
    to_date_str = request.GET.get('to', today.isoformat())
    from_date = date.fromisoformat(from_date_str)
    to_date = date.fromisoformat(to_date_str)

    # --- Site Key filter options ---
    site_keys = (
        Event.objects.exclude(site_key__isnull=True)
        .values_list('site_key', flat=True)
        .distinct()
        .order_by('site_key')
    )
    active_site_key = request.GET.get('site_key', '')

    # --- Base querysets ---
    events_base_qs = Event.objects.all()
    leads_base_qs = Lead.objects.all()
    sessions_base_qs = Session.objects.all()

    if active_site_key:
        events_base_qs = events_base_qs.filter(site_key=active_site_key)
        # Leads are linked via Event.lead FK; restrict by events with site_key
        leads_base_qs = leads_base_qs.filter(event__site_key=active_site_key).distinct()
        sessions_base_qs = sessions_base_qs.filter(site_key=active_site_key)

    # --- Date filter ---
    events_qs = events_base_qs.filter(created_at__date__range=[from_date, to_date])
    leads_qs = leads_base_qs.filter(created_at__date__range=[from_date, to_date])
    sessions_qs = sessions_base_qs.filter(first_seen__date__range=[from_date, to_date])

    # --- CSV export ---
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="events_export.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Event UUID', 'Event Type', 'Site Key', 'Session ID', 'Client ID',
            'URL', 'Page Title', 'Referrer', 'UTM Source', 'UTM Campaign', 'Created At'
        ])
        for e in events_qs.select_related('session'):
            event_data = e.event_data or {}
            url = e.url or event_data.get('meta_url', '')
            page_title = e.page_title or event_data.get('meta_page_title', '')
            referrer = e.referrer or event_data.get('meta_referrer', '')
            utm_source = e.utm_source or event_data.get('utm_source', '')
            utm_campaign = e.utm_campaign or event_data.get('utm_campaign', '')

            writer.writerow([
                str(e.event_id),
                e.event_type,
                e.site_key,
                e.session.session_id if e.session else '',
                e.session.client_id if e.session else '',
                url,
                page_title,
                referrer,
                utm_source,
                utm_campaign,
                e.created_at.isoformat(),
            ])
        return response

    # --- KPIs ---
    total_events = events_qs.count()
    page_loads = events_qs.filter(event_type='page_load').count()
    form_submits = events_qs.filter(event_type='form_submission').count()

    # Unique visitors = distinct client_id across sessions in window
    unique_visitors = (
        sessions_qs
        .exclude(client_id__isnull=True).exclude(client_id='')
        .values('client_id').distinct().count()
    )

    # Identified users (from Session model)
    identified_users = (
        sessions_qs
        .filter(Q(user_external_id__isnull=False) | Q(user_email__isnull=False))
        .distinct()
        .count()
    )

    # Registrations / Logins via custom_event names
    new_registrations = events_qs.filter(
        event_type='custom_event',
        event_data__event_name__in=['user_registered', 'user_register']
    ).count()
    logins = events_qs.filter(
        event_type='custom_event',
        event_data__event_name__in=['user_logged_in', 'user_login']
    ).count()

    # --- Time series (daily) ---
    daily_counts = (
        events_qs
        .annotate(date=models.functions.TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    by_day = {row['date']: row['count'] for row in daily_counts}
    days, counts = [], []
    cur = from_date
    while cur <= to_date:
        days.append(cur.isoformat())
        counts.append(by_day.get(cur, 0))
        cur += timedelta(days=1)

    # --- Top pages / campaigns ---
    top_pages = (
        events_qs.annotate(page_url=Coalesce(
            'url', models.F('event_data__meta_url'),
            output_field=models.TextField()
        ))
        .exclude(page_url__isnull=True).exclude(page_url='')
        .values('page_url')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    top_campaigns = (
        events_qs
        .annotate(source=Coalesce(
            'utm_source', models.F('event_data__utm_source'),
            output_field=models.TextField()
        ))
        .annotate(campaign=Coalesce(
            'utm_campaign', models.F('event_data__utm_campaign'),
            output_field=models.TextField()
        ))
        .values('source', 'campaign')
        .exclude(source__isnull=True).exclude(source='')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    # --- New sections from payload ---

    # Top Referrers
    top_referrers = (
        events_qs
        .annotate(ref=Coalesce('referrer', models.F('event_data__meta_referrer')))
        .exclude(ref__isnull=True).exclude(ref='')
        .values('ref')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    # Languages
    lang_breakdown = (
        events_qs
        .annotate(lang=Coalesce('language', models.F('event_data__meta_language')))
        .exclude(lang__isnull=True).exclude(lang='')
        .values('lang')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    # Device mix (bucketed by viewport width)
    vw_expr = Coalesce(models.F('event_data__meta_vw'), Value(0))
    vw_int = Cast(vw_expr, IntegerField())
    device_mix = (
        events_qs
        .annotate(vw=vw_int)
        .annotate(bucket=models.Case(
            models.When(vw__lt=640, then=Value('Mobile')),
            models.When(vw__lt=1024, then=Value('Tablet')),
            default=Value('Desktop'),
            output_field=CharField(),
        ))
        .values('bucket')
        .annotate(events=Count('id'))
        .order_by('-events')
    )

    # Top Searches (only custom_event search, non-masked)
    top_searches = (
        events_qs.filter(
            event_type='custom_event',
            event_data__event_name='search',
            event_data__field__masked=False,
        )
        .values('event_data__field__label', 'event_data__field__value')
        .annotate(times=Count('id'))
        .order_by('-times')[:10]
    )

    # --- Leads ---
    recent_leads = leads_qs.order_by('-created_at')[:10]

    # --- Recent identified users (from Session model) ---
    recent_users = sessions_qs.filter(
        Q(user_external_id__isnull=False) | Q(user_email__isnull=False)
    ).order_by('-last_seen')[:10]

    context = {
        'site_keys': site_keys,
        'active_site_key': active_site_key,
        'from': from_date_str,
        'to': to_date_str,

        'total_events': total_events,
        'page_loads': page_loads,
        'form_submits': form_submits,
        'unique_visitors': unique_visitors,

        'new_leads': leads_qs.count(),
        'recent_leads': recent_leads,

        'identified_users': identified_users,
        'new_registrations': new_registrations,
        'logins': logins,
        'recent_users': recent_users,

        'top_pages': top_pages,
        'top_campaigns': top_campaigns,

        'top_referrers': top_referrers,
        'lang_breakdown': lang_breakdown,
        'device_mix': list(device_mix),
        'top_searches': top_searches,

        'days_json': json.dumps(days),
        'counts_json': json.dumps(counts),
    }
    return render(request, 'dashboard.html', context)
