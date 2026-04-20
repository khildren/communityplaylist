from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Event, Artist, Venue, PromoterProfile


class EventSitemap(Sitemap):
    changefreq = 'weekly'
    priority   = 0.9

    def items(self):
        return Event.objects.filter(status='approved').order_by('-start_date')

    def lastmod(self, obj):
        return obj.start_date

    def location(self, obj):
        return f'/events/{obj.slug}/'


class ArtistSitemap(Sitemap):
    changefreq = 'monthly'
    priority   = 0.7

    def items(self):
        return Artist.objects.filter(is_stub=False).order_by('name')

    def location(self, obj):
        return f'/artists/{obj.slug}/'


class VenueSitemap(Sitemap):
    changefreq = 'weekly'
    priority   = 0.8

    def items(self):
        return Venue.objects.filter(active=True).order_by('name')

    def location(self, obj):
        return f'/venues/{obj.slug}/'


class PromoterSitemap(Sitemap):
    changefreq = 'monthly'
    priority   = 0.7

    def items(self):
        return PromoterProfile.objects.filter(is_public=True).order_by('name')

    def location(self, obj):
        return f'/promoters/{obj.slug}/'


class StaticSitemap(Sitemap):
    changefreq = 'daily'
    priority   = 1.0

    def items(self):
        return ['event_list', 'promoter_list', 'artist_list', 'venue_list']

    def location(self, item):
        return reverse(item)
