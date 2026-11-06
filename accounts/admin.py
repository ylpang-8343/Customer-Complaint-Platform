from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Factory, FactoryPIC, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "email", "role", "factory", "department", "is_active")
    list_filter = ("role", "factory", "is_active")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Complaint platform", {"fields": ("role", "factory", "department", "deputy", "away_until")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (None, {"fields": ("email", "role", "factory", "department")}),
    )


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "factory_manager")


@admin.register(FactoryPIC)
class FactoryPICAdmin(admin.ModelAdmin):
    list_display = ("factory", "designation", "user", "is_active")
    list_filter = ("factory", "is_active")
