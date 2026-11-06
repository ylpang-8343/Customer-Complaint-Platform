from django.contrib import admin

from .models import ComplaintMethod, ComplaintSubType, ComplaintType, LabTest


class ComplaintSubTypeInline(admin.TabularInline):
    model = ComplaintSubType
    extra = 1


@admin.register(ComplaintType)
class ComplaintTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "requires_details", "reminder_days", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    inlines = [ComplaintSubTypeInline]


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(ComplaintMethod)
class ComplaintMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
