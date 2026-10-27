from django.contrib import admin
from django import forms
from .models import KitLot
from .services import save_kit_lot


class KitLotAdminForm(forms.ModelForm):
    reason = forms.CharField(
        max_length=250,
        label="Reason for stock update",
        help_text="Explain why this stock quantity is being added or changed.",
    )

    class Meta:
        model = KitLot
        exclude = ("owner", "reorder_threshold", "average_weekly_use", "lead_time_days", "storage_location")

@admin.register(KitLot)
class KitLotAdmin(admin.ModelAdmin):
    form = KitLotAdminForm
    list_display = ("kit_name", "lot_number", "expiry_date", "quantity_available", "active")
    list_filter = ("active", "expiry_date")
    search_fields = ("kit_name", "kit_code", "lot_number")
    exclude = ("owner", "reorder_threshold", "average_weekly_use", "lead_time_days", "storage_location")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(owner=request.user)

    def has_add_permission(self, request):
        return request.user.has_perm("inventory.can_manage_inventory")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("inventory.can_manage_inventory")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("inventory.can_manage_inventory")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
        save_kit_lot(kit=obj, actor=request.user, reason=form.cleaned_data["reason"])

