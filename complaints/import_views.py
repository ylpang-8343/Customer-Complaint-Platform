"""Upload of past complaints: download the template, upload a file, check it, then import it (Coordinator only)."""

from io import BytesIO
from pathlib import Path

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import importer, people
from .models import ImportBatch

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PROBLEM_LIMIT = 300
PREVIEW_LIMIT = 50


class UploadForm(forms.Form):
    file = forms.FileField(label="Spreadsheet (.xlsx)")

    def clean_file(self):
        file = self.cleaned_data["file"]
        if Path(file.name).suffix.lower() != ".xlsx":
            raise forms.ValidationError("Upload an Excel workbook ending in .xlsx. Use the template.")
        if file.size > importer.MAX_FILE_MB * 1024 * 1024:
            raise forms.ValidationError(f"The file is larger than {importer.MAX_FILE_MB} MB.")
        return file


def _coordinator_only(request):
    if people.is_coordinator(request.user):
        return None
    messages.error(request, "Only the Coordinator can import past complaints.")
    return redirect("home")


@login_required
def import_home(request):
    refused = _coordinator_only(request)
    if refused:
        return refused
    form = UploadForm(request.POST, request.FILES) if request.method == "POST" else UploadForm()
    if request.method == "POST" and form.is_valid():
        file = form.cleaned_data["file"]
        batch = ImportBatch.objects.create(uploaded_by=request.user, original_name=Path(file.name).name[:255], file=file)
        return redirect("import_preview", pk=batch.pk)
    return render(
        request,
        "import_home.html",
        {
            "form": form,
            "batches": ImportBatch.objects.select_related("uploaded_by")[:20],
            "columns": importer.COLUMNS,
            "max_rows": importer.MAX_ROWS,
            "max_mb": importer.MAX_FILE_MB,
        },
    )


@login_required
def import_template(request):
    refused = _coordinator_only(request)
    if refused:
        return refused
    response = HttpResponse(importer.build_template(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = 'attachment; filename="complaint-import-template.xlsx"'
    return response


@login_required
def import_preview(request, pk):
    refused = _coordinator_only(request)
    if refused:
        return refused
    batch = get_object_or_404(ImportBatch, pk=pk)

    if request.method == "POST" and batch.status == ImportBatch.Status.PREVIEW:
        if "discard" in request.POST:
            batch.file.delete(save=False)
            batch.status = ImportBatch.Status.DISCARDED
            batch.save()
            messages.info(request, f"{batch.original_name} was discarded. Nothing was imported.")
            return redirect("import_home")
        try:
            created = importer.run_import(batch, request.user)
        except importer.ImportProblem as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Imported {len(created)} complaint{'' if len(created) == 1 else 's'} from {batch.original_name}.")
        return redirect("import_preview", pk=batch.pk)

    context = {"batch": batch}
    if batch.status == ImportBatch.Status.PREVIEW:
        try:
            with batch.file.open("rb") as handle:
                analysis = importer.analyse(BytesIO(handle.read()))
        except FileNotFoundError:
            analysis = importer.Analysis(file_errors=["The uploaded file is no longer in storage. Upload it again."])
        problems = [(row.row, column, message) for row in analysis.rows for column, message in row.errors]
        context.update(
            analysis=analysis,
            problems=problems[:PROBLEM_LIMIT],
            more_problems=max(0, len(problems) - PROBLEM_LIMIT),
            preview=analysis.rows[:PREVIEW_LIMIT],
            more_rows=max(0, len(analysis.rows) - PREVIEW_LIMIT),
            warning_count=sum(len(row.warnings) for row in analysis.rows),
            ok_rows=sum(1 for row in analysis.rows if row.ok),
        )
    elif batch.status == ImportBatch.Status.IMPORTED:
        context["imported"] = batch.complaints.select_related("factory", "complaint_type").order_by("created_at", "id")[:PREVIEW_LIMIT]
    return render(request, "import_preview.html", context)
