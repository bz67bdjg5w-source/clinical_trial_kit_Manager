
@login_required
def quick_update_stock(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.method == "POST":
        study_id = request.POST.get("study_id", "").strip()
    else:
        study_id = request.GET.get("study", "").strip()

    study = None

    if study_id:
        if not study_id.isdecimal():
            from django.http import Http404
            raise Http404("Study not found.")

        study = get_object_or_404(
            Study,
            pk=int(study_id),
            owner=request.user,
        )

    if request.method == "GET":
        form = QuickStockUpdateForm(
            user=request.user,
            study=study,
        )

    else:
        expected_token = request.session.get(
            QUICK_UPDATE_TOKEN_SESSION_KEY,
            "",
        )

        submitted_token = request.POST.get(
            "submission_token",
            "",
        )

        if (
            not expected_token
            or not secrets.compare_digest(
                expected_token,
                submitted_token,
            )
        ):
            messages.error(
                request,
                "This stock update was already submitted or has expired. Please try again.",
            )

            if study:
                return redirect(
                    f"{reverse('quick_update_stock')}?study={study.pk}"
                )

            return redirect("quick_update_stock")

        request.session.pop(
            QUICK_UPDATE_TOKEN_SESSION_KEY,
            None,
        )

        form = QuickStockUpdateForm(
            request.POST,
            user=request.user,
            study=study,
        )

        if form.is_valid():
            try:
                record_kit_usage(
                    kit_id=form.cleaned_data["kit"].pk,
                    actor=request.user,
                    quantity_used=form.cleaned_data["quantity_used"],
                    reason=form.cleaned_data["reason"],
                )

            except (PermissionDenied, ValidationError) as error:
                form.add_error(
                    "quantity_used",
                    error,
                )

            else:
                messages.success(
                    request,
                    "Stock usage recorded.",
                )

                if study:
                    return redirect(
                        f"{reverse('quick_update_stock')}?study={study.pk}"
                    )

                return redirect("quick_update_stock")

    submission_token = secrets.token_urlsafe(32)

    request.session[QUICK_UPDATE_TOKEN_SESSION_KEY] = (
        submission_token
    )

    return render(
        request,
        "inventory/quick_update_stock.html",
        {
            "form": form,
            "submission_token": submission_token,
            "study": study,
            "selected_kit_id": (
                form.data.get("kit", "")
                if form.is_bound
                else ""
            ),
        },
    )
