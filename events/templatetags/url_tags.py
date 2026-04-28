from django import template
from events.utils.url_safety import sanitize_url, display_domain, is_safe_url

register = template.Library()


@register.filter
def safe_href(url):
    """Return url if safe, else ''. Use in href attrs: href="{{ obj.website|safe_href }}"."""
    return sanitize_url(url or '')


@register.filter
def domain(url):
    """Return bare domain for display: {{ obj.website|domain }} → 'soundcloud.com'."""
    return display_domain(url or '')


@register.filter
def is_safe(url):
    """Boolean — use in {% if obj.website|is_safe %} guards."""
    return is_safe_url(url or '')


@register.filter
def get_item(d, key):
    """Dict lookup in templates: {{ my_dict|get_item:key }}"""
    return d.get(key)
