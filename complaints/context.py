STEP_PAGES = {
    "home", "complaint_detail", "complaint_assign", "complaint_delete", "complaint_investigation",
    "complaint_lab", "complaint_scar", "complaint_scar_return", "complaint_hod", "complaint_fm",
    "complaint_reply", "complaint_verify",
}  # fmt: skip


def navigation(request):
    """Which menu entry is highlighted. Every step of a complaint belongs under My complaints."""
    name = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
    if name in STEP_PAGES:
        return {"nav_current": "home"}
    if name in ("complaint_new", "complaint_edit"):
        return {"nav_current": "new"}
    if name in ("notifications", "run_reminders", "send_test_email", "dashboard"):
        return {"nav_current": name}
    if name in ("masterdata_home", "masterdata_template", "masterdata_upload"):
        return {"nav_current": "masterdata"}
    if name in ("import_home", "import_template", "import_preview"):
        return {"nav_current": "import"}
    if name in ("complaint_log", "log_export"):
        return {"nav_current": "log"}
    return {"nav_current": "help" if name == "help" else ""}


def demo(request):
    """On a public demo site the sign-in page tells visitors which users they can try."""
    from django.conf import settings

    if not settings.DEMO_MODE:
        return {"demo_mode": False}
    from accounts.management.commands.seed import DEMO_PASSWORD

    return {"demo_mode": True, "demo_password": DEMO_PASSWORD}
