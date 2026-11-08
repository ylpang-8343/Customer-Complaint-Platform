"""Send the reminders that are due. Run it once a day (see the README for how to schedule it).

    python manage.py send_reminders             # send them
    python manage.py send_reminders --dry-run   # only list who would be reminded
"""

from django.core.management.base import BaseCommand

from complaints.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Email the people who have a complaint waiting for them for longer than its reminder days."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the reminders without sending them.")

    def handle(self, *args, dry_run=False, **options):
        results = send_due_reminders(dry_run=dry_run)
        for item in results:
            names = ", ".join(str(user) for user in item["recipients"]) or "nobody"
            outcome = "would remind" if dry_run else ("reminded" if item["sent"] else "FAILED to remind")
            self.stdout.write(
                f"{outcome} {names} about {item['complaint'].reference} "
                f"({item['days']} days, reminder {item['number']})"
            )
        sent = sum(1 for item in results if item["sent"])
        failed = sum(1 for item in results if not item["sent"]) if not dry_run else 0
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"{len(results)} reminder(s) are due."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{sent} reminder(s) sent."))
            if failed:
                self.stderr.write(self.style.ERROR(f"{failed} could not be sent; see the notification log."))
