from django.contrib import admin

from .models import ComplaintMethod, ComplaintSubType, ComplaintType, Customer, LabTest, PackSize, Product, RoutingRule


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


# The lists the complaint form chooses from. Single entries are edited here; whole lists are uploaded under Master data.
@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_editable = ("is_active",)
    search_fields = ("code", "name")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_editable = ("is_active",)
    search_fields = ("code", "name")


@admin.register(PackSize)
class PackSizeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name",)


@admin.register(RoutingRule)
class RoutingRuleAdmin(admin.ModelAdmin):
    """The routing table: which department investigates each kind of complaint. A type with no rule goes to the Coordinator."""

    list_display = ("complaint_type", "scope", "department")
    list_filter = ("department", "scope")
