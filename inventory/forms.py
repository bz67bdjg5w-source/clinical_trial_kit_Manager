
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Study, KitLot


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")

    def clean_username(self):
        username = self.cleaned_data["username"]
        user_model = get_user_model()

        if user_model._default_manager.filter(
            username__iexact=username
        ).exists():
            raise forms.ValidationError(
                "A user with that username already exists."
            )

        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        user_model = get_user_model()

        if user_model._default_manager.filter(
            email__iexact=email
        ).exists():
            raise forms.ValidationError(
                "An account with that email address already exists."
            )

        return email


class ResendSignupConfirmationForm(forms.Form):
    email = forms.EmailField(required=True)


class StudyForm(forms.ModelForm):
    class Meta:
        model = Study
        fields = ["name", "study_code", "description"]
        labels = {
            "name": "Study or trial name",
            "study_code": "Study code",
            "description": "Description",
        }


class KitLotForm(forms.ModelForm):
    class Meta:
        model = KitLot
        fields = [
            "kit_code",
            "lot_number",
            "expiry_date",
            "quantity_available",
            "reorder_threshold",
            "lead_time_days",
            "supplier_or_lab",
            "notes",
            "active",
        ]
        labels = {
            "kit_code": "Lab kit code",
            "lot_number": "Lot number",
            "quantity_available": "Quantity available",
            "reorder_threshold": "Low-stock alert threshold",
            "lead_time_days": "Supplier lead time (days)",
        }
        help_texts = {
            "reorder_threshold": (
                "Show a low-stock alert when available quantity "
                "is equal to or below this number."
            ),
        }
        widgets = {
            "expiry_date": forms.DateInput(
                attrs={"type": "date"}
            ),
            "quantity_available": forms.NumberInput(
                attrs={"min": 0, "step": 1}
            ),
            "reorder_threshold": forms.NumberInput(
                attrs={"min": 0, "step": 1}
            ),
            "lead_time_days": forms.NumberInput(
                attrs={"min": 0, "step": 1}
            ),
        }


class KitLotChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, kit):
        return (
            f"{kit.study.name} · {kit.kit_name} · "
            f"Lot {kit.lot_number} · "
            f"{kit.quantity_available} available"
        )


class QuickStockUpdateForm(forms.Form):
    kit = KitLotChoiceField(
        queryset=KitLot.objects.none(),
        label="Select lab kit",
    )
    quantity_used = forms.IntegerField(
        min_value=1,
        label="Quantity used",
        widget=forms.NumberInput(attrs={"min": 1, "step": 1}),
    )
    reason = forms.CharField(
        max_length=250,
        required=False,
        label="Reason",
        widget=forms.TextInput(attrs={"maxlength": 250}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kit"].queryset = (
            KitLot.objects.filter(
                owner=user,
                study__owner=user,
                active=True,
            )
            .select_related("study")
            .order_by("study__name", "kit_name", "lot_number")
        )

    def clean_quantity_used(self):
        quantity_used = self.cleaned_data["quantity_used"]
        kit = self.cleaned_data.get("kit")

        if kit and quantity_used > kit.quantity_available:
            raise forms.ValidationError(
                "Cannot use more kits than the available stock."
            )

        return quantity_used
