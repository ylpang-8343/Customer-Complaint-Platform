from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path

from complaints import views as complaint_views
from complaints import import_views as imports
from complaints import report_views as reports
from complaints import workflow_views as steps

admin.site.site_header = "BEST Group Complaint Platform administration"
admin.site.site_title = "Complaint Platform admin"

urlpatterns = [
    path("healthz/", lambda request: HttpResponse("ok", content_type="text/plain"), name="healthz"),
    path("", complaint_views.home, name="home"),
    path("help/", complaint_views.help_page, name="help"),
    path("notifications/", complaint_views.notification_log, name="notifications"),
    path("notifications/run-reminders/", complaint_views.run_reminders, name="run_reminders"),
    path("notifications/test-email/", complaint_views.send_test_email, name="send_test_email"),
    path("dashboard/", reports.dashboard, name="dashboard"),
    path("log/", reports.complaint_log, name="complaint_log"),
    path("log/export/", reports.log_export, name="log_export"),
    path("import/", imports.import_home, name="import_home"),
    path("import/template.xlsx", imports.import_template, name="import_template"),
    path("import/<int:pk>/", imports.import_preview, name="import_preview"),
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
    path("complaints/<int:pk>/report.pdf", steps.complaint_pdf, name="complaint_pdf"),
    path("complaints/<int:pk>/audit-pack.zip", steps.complaint_audit_pack, name="complaint_audit_pack"),
    path("scars/<int:pk>/pdf/", steps.scar_document, name="scar_pdf"),
    path("attachments/<int:pk>/", complaint_views.attachment_download, name="attachment_download"),
    path("attachments/<int:pk>/delete/", complaint_views.attachment_delete, name="attachment_delete"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
