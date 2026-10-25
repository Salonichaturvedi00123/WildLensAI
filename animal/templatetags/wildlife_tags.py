"""
Custom Django template filters for safe wildlife dossier and markdown rendering.
"""

from django import template
from django.utils.safestring import mark_safe
from animal.utils import (
    extract_clean_message_text,
    render_markdown_safe,
    is_valid_source_url,
    sanitize_source_references,
    organize_dossier_sections,
)

register = template.Library()


@register.simple_tag(name="get_dossier_sections")
def get_dossier_sections_tag(wildlife_info, source_references=None):
    """Organize wildlife information and sources into structured section cards."""
    return organize_dossier_sections(wildlife_info, source_references)


@register.filter(name="dossier_sections")
def dossier_sections_filter(wildlife_info, source_references=None):
    """Filter version of get_dossier_sections."""
    return organize_dossier_sections(wildlife_info, source_references)


@register.filter(name="render_dossier")
def render_dossier_filter(value):
    """
    Renders clean, structured markdown for the Grounded Wildlife Dossier.
    Safely unwraps content-block lists or dict structures, strips provider metadata,
    and converts Markdown into XSS-safe HTML.
    """
    if not value:
        return ""
    html_output = render_markdown_safe(value)
    return mark_safe(html_output)


@register.filter(name="render_markdown")
def render_markdown_filter(value):
    """Alias for render_dossier."""
    return render_dossier_filter(value)


@register.filter(name="clean_text")
def clean_text_filter(value):
    """Extracts human-readable text without converting to HTML."""
    return extract_clean_message_text(value)


@register.filter(name="valid_sources")
def valid_sources_filter(references):
    """Returns only validated external source references."""
    return sanitize_source_references(references)


@register.filter(name="is_valid_url")
def is_valid_url_filter(url):
    """Check if a URL is valid."""
    return is_valid_source_url(url)


# Direct aliases for unit testing / Python imports
render_dossier = render_dossier_filter
render_markdown = render_markdown_filter
clean_text = clean_text_filter
valid_sources = valid_sources_filter
is_valid_url = is_valid_url_filter
get_dossier_sections = get_dossier_sections_tag
dossier_sections = dossier_sections_filter
