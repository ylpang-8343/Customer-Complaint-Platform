from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.access import factories_for


@login_required
def home(request):
    return render(request, "home.html", {"factories": factories_for(request.user)})
