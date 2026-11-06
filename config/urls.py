from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from complaints import views as complaint_views

admin.site.site_header = "BEST Group Complaint Platform administration"
admin.site.site_title = "Complaint Platform admin"

urlpatterns = [
    path("", complaint_views.home, name="home"),
    path("help/", complaint_views.help_page, name="help"),
    path("complaints/new/", complaint_views.complaint_form, name="complaint_new"),
    path("complaints/<int:pk>/", complaint_views.complaint_detail, name="complaint_detail"),
    path("complaints/<int:pk>/edit/", complaint_views.complaint_form, name="complaint_edit"),
    path("attachments/<int:pk>/", complaint_views.attachment_download, name="attachment_download"),
    path("attachments/<int:pk>/delete/", complaint_views.attachment_delete, name="attachment_delete"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
