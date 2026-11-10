"""Create the twelve demo complaints (run with: python manage.py shell < tools/screenshots/demo_data.py)."""
from complaints.demo import create_demo_complaints

print("created", create_demo_complaints(force=True), "complaints")
