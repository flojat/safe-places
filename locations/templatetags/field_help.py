"""Hilfetexte zu Objektfeldern ("i"-Overlay), gepflegt durch die Administration.

Ausgabe nur fuer vollstaendig angemeldete Konten, nie fuer Gaeste. Die Texte werden als JSON
eingebettet und vom Skript ausschliesslich per textContent dargestellt.
"""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html, json_script

from ..models import FieldHelp
from ..permissions import is_fully_verified

register = template.Library()


def help_texts() -> dict:
    return {h.field: {"label": h.get_field_display(), "text": h.text}
            for h in FieldHelp.objects.exclude(text="")}


@register.simple_tag(takes_context=True)
def field_help(context):
    request = context.get("request")
    if request is None or not is_fully_verified(request):
        return ""
    data = help_texts()
    if not data:
        return ""
    return format_html('<link rel="stylesheet" href="{}">{}<script src="{}"></script>',
                       static("css/field_help.css"), json_script(data, "field-help-data"),
                       static("js/field_help.js"))
