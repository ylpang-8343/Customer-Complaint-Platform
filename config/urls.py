from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from complaints import views as complaint_views

admin.site.site_header = "BEST Group Complaint Platform administration"
admin.site.site_title = "Complaint Platform admin"

urlpatterns = [
    path("", complaint_views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
