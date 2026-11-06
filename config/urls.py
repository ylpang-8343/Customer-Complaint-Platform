from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from complaints import views as complaint_views
from complaints import workflow_views as steps

admin.site.site_header = "BEST Group Complaint Platform administration"
admin.site.site_title = "Complaint Platform admin"

urlpatterns = [
    path("", complaint_views.home, name="home"),
    path("help/", complaint_views.help_page, name="help"),
    path("complaints/new/", complaint_views.complaint_form, name="complaint_new"),
    path("complaints/<int:pk>/", complaint_views.complaint_detail, name="complaint_detail"),
    path("complaints/<int:pk>/edit/", complaint_views.complaint_form, name="complaint_edit"),
    path("complaints/<int:pk>/assign/", steps.complaint_assign, name="complaint_assign"),
    path("complaints/<int:pk>/delete/", steps.complaint_delete, name="complaint_delete"),
    path("complaints/<int:pk>/investigation/", steps.complaint_investigation, name="complaint_investigation"),
    path("complaints/<int:pk>/laboratory/", steps.complaint_lab, name="complaint_lab"),
    path("complaints/<int:pk>/scar/", steps.complaint_scar, name="complaint_scar"),
    path("complaints/<int:pk>/scar/return/", steps.complaint_scar_return, name="complaint_scar_return"),
    path("complaints/<int:pk>/hod/", steps.complaint_hod, name="complaint_hod"),
    path("complaints/<int:pk>/fm/", steps.complaint_fm, name="complaint_fm"),
    path("complaints/<int:pk>/reply/", steps.complaint_reply, name="complaint_reply"),
    path("complaints/<int:pk>/verify/", steps.complaint_verify, name="complaint_verify"),
    path("complaints/<int:pk>/summary.pdf", steps.complaint_pdf, name="complaint_pdf"),
    path("scars/<int:pk>/pdf/", steps.scar_document, name="scar_pdf"),
    path("attachments/<int:pk>/", complaint_views.attachment_download, name="attachment_download"),
    path("attachments/<int:pk>/delete/", complaint_views.attachment_delete, name="attachment_delete"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
