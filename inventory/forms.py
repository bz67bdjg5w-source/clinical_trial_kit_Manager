
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

    def __init__(self, *args, user, study=None, **kwargs):
        super().__init__(*args, **kwargs)

        kits = KitLot.objects.filter(
            owner=user,
            study__owner=user,
            active=True,
        )

        if study is not None:
            kits = kits.filter(study=study)

        self.fields["kit"].queryset = (
            kits.select_related("study")
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
