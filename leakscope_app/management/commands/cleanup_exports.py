from django.core.management.base import BaseCommand
from leakscope.exporters.storage import cleanup_expired_exports


class Command(BaseCommand):
    help = "Remove expired temporary exports without modifying stored findings."

    def handle(self, *args, **options):
        count = cleanup_expired_exports()
        self.stdout.write("Removed %s expired exports." % count)
