import signal
import threading

from django.core.management.base import BaseCommand
from leakscope.phase3.certstream import consume_stream


class Command(BaseCommand):
    help = 'Consume the explicitly selected built-in or external certificate stream'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Stop after one certificate message or 60 seconds')
        parser.add_argument('--max-events', type=int, default=0)
        parser.add_argument('--max-seconds', type=int, default=0)

    def handle(self, *args, **options):
        stop = threading.Event()
        previous = {}
        for name in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[name] = signal.signal(name, lambda *_: stop.set())
            except ValueError:
                # Django command tests may run outside the process main thread.
                pass
        try:
            count = consume_stream(stop_event=stop,
                max_events=1 if options['once'] else max(0, options['max_events']),
                max_seconds=(options['max_seconds'] or 60) if options['once'] else max(0, options['max_seconds']))
        finally:
            for name, handler in previous.items():
                signal.signal(name, handler)
        self.stdout.write(f'Certificate consumer stopped after {count} certificate message(s)')
