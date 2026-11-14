from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Department, Factory, FactoryPIC, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "email", "role", "factory", "factories_covered", "dept", "is_active")
    list_filter = ("role", "factory", "assigned_factories", "dept", "is_active")
    filter_horizontal = BaseUserAdmin.filter_horizontal + ("assigned_factories",)
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Complaint platform: access",
            {
                "description": "What the user may see. A factory user sees their factory (and any assigned below); a trader or "
                "group PIC can be given several factories. The Coordinator and top management see every factory.",
                "fields": ("role", "factory", "assigned_factories", "dept", "department", "deputy", "away_until"),
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (None, {"fields": ("email", "role", "factory", "dept", "department")}),
    )

    @admin.display(description="Factories covered")
    def factories_covered(self, user):
        if user.sees_all_factories:
            return "All"
        codes = sorted({f.code for f in user.assigned_factories.all()} | ({user.factory.code} if user.factory_id else set()))
        return ", ".join(codes) or "-"


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "factory_manager")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order")
    list_editable = ("sort_order",)


@admin.register(FactoryPIC)
class FactoryPICAdmin(admin.ModelAdmin):
    list_display = ("factory", "department", "designation", "user", "is_active")
    list_filter = ("factory", "department", "is_active")
