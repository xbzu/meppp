from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .forms import CloseReportForm, TriageReportForm
from .models import ModerationDecision, Report, ReportStatus
from .services import close_report, triage_report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    change_form_template = "admin/moderation/report/change_form.html"
    list_display = (
        "public_id",
        "subject_type",
        "reason",
        "status",
        "assigned_to",
        "reporter",
        "created_at",
    )
    list_filter = ("status", "subject_type", "reason", "created_at")
    search_fields = ("details", "reporter__username", "subject_public_id")
    readonly_fields = (
        "public_id",
        "reporter",
        "subject_type",
        "subject_public_id",
        "reason",
        "details",
        "status",
        "assigned_to",
        "resolved_by",
        "resolved_at",
        "created_at",
        "updated_at",
    )
    actions = ()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                "<path:object_id>/workflow/",
                self.admin_site.admin_view(self.workflow_view),
                name=f"{opts.app_label}_{opts.model_name}_workflow",
            )
        ]
        return custom_urls + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method != "GET":
            raise PermissionDenied("Use the moderation workflow to change reports")
        report = self.get_object(request, unquote(object_id))
        workflow_url = None
        if (
            report is not None
            and report.status in {ReportStatus.OPEN, ReportStatus.TRIAGED}
            and self.has_change_permission(request, report)
        ):
            workflow_url = reverse(
                "admin:moderation_report_workflow",
                args=[report.pk],
            )
        extra_context = {
            **(extra_context or {}),
            "moderation_workflow_url": workflow_url,
            "show_save": False,
            "show_save_and_continue": False,
            "show_save_and_add_another": False,
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def workflow_view(self, request, object_id):
        report = self.get_object(request, unquote(object_id))
        if report is None:
            raise Http404("Report not found")
        if not self.has_change_permission(request, report):
            raise PermissionDenied

        operation = request.POST.get("operation") if request.method == "POST" else None
        triage_form = TriageReportForm(
            request.POST if operation == "triage" else None,
            prefix="triage",
            initial={"assigned_to": request.user},
        )
        close_form = CloseReportForm(
            request.POST if operation == "close" else None,
            prefix="close",
            report=report,
        )

        if request.method == "POST":
            if operation == "triage":
                if triage_form.is_valid():
                    try:
                        triage_report(
                            report=report,
                            actor=request.user,
                            assigned_to=triage_form.cleaned_data["assigned_to"],
                            reason=triage_form.cleaned_data["reason"],
                        )
                    except ValidationError as error:
                        triage_form.add_error(None, error)
                    else:
                        self.message_user(request, "举报已分派并标记为处理中。", messages.SUCCESS)
                        return self._change_redirect(report)
            elif operation == "close":
                if close_form.is_valid():
                    try:
                        close_report(
                            report=report,
                            actor=request.user,
                            status=close_form.cleaned_data["status"],
                            action=close_form.cleaned_data["action"],
                            reason=close_form.cleaned_data["reason"],
                        )
                    except ValidationError as error:
                        close_form.add_error(None, error)
                    else:
                        self.message_user(
                            request,
                            "举报处置已完成并写入审计记录。",
                            messages.SUCCESS,
                        )
                        return self._change_redirect(report)
            else:
                raise SuspiciousOperation("Unknown moderation workflow operation")
            report.refresh_from_db()

        opts = self.model._meta
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "original": report,
            "title": "处理举报",
            "triage_form": triage_form,
            "close_form": close_form,
            "show_triage": report.status == ReportStatus.OPEN or operation == "triage",
            "show_close": (
                report.status in {ReportStatus.OPEN, ReportStatus.TRIAGED} or operation == "close"
            ),
            "change_url": reverse("admin:moderation_report_change", args=[report.pk]),
            "media": self.media + triage_form.media + close_form.media,
        }
        return TemplateResponse(
            request,
            "admin/moderation/report/workflow.html",
            context,
        )

    @staticmethod
    def _change_redirect(report):
        return HttpResponseRedirect(reverse("admin:moderation_report_change", args=[report.pk]))


@admin.register(ModerationDecision)
class ModerationDecisionAdmin(admin.ModelAdmin):
    list_display = ("report", "action", "actor", "created_at")
    readonly_fields = (
        "public_id",
        "report",
        "actor",
        "action",
        "reason",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
