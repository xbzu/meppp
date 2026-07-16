from django import forms
from django.contrib.auth import get_user_model

from .models import ModerationAction, ReportStatus, SubjectType
from .services import MAX_MODERATION_REASON_LENGTH

SUBJECT_ACTIONS = {
    SubjectType.USER: (
        ModerationAction.SUSPEND_USER,
        ModerationAction.RESTORE_USER,
    ),
    SubjectType.ENTRY: (
        ModerationAction.HIDE_ENTRY,
        ModerationAction.RESTORE_ENTRY,
    ),
    SubjectType.COMMENT: (
        ModerationAction.HIDE_COMMENT,
        ModerationAction.RESTORE_COMMENT,
    ),
}


class TriageReportForm(forms.Form):
    assigned_to = forms.ModelChoiceField(
        label="分派给",
        queryset=get_user_model().objects.none(),
        help_text="只能分派给拥有举报处置权限的在职后台成员。",
    )
    reason = forms.CharField(
        label="分派原因",
        max_length=MAX_MODERATION_REASON_LENGTH,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        staff = user_model.objects.filter(is_active=True, is_staff=True).order_by("username", "pk")
        moderator_ids = [
            candidate.pk for candidate in staff if candidate.has_perm("moderation.change_report")
        ]
        self.fields["assigned_to"].queryset = user_model.objects.filter(
            pk__in=moderator_ids
        ).order_by("username", "pk")

    def clean_assigned_to(self):
        assigned_to = self.cleaned_data["assigned_to"]
        if not assigned_to.has_perm("moderation.change_report"):
            raise forms.ValidationError("所选成员没有举报处置权限。")
        return assigned_to


class CloseReportForm(forms.Form):
    status = forms.ChoiceField(
        label="处理结论",
        choices=(
            (ReportStatus.RESOLVED, ReportStatus.RESOLVED.label),
            (ReportStatus.REJECTED, ReportStatus.REJECTED.label),
        ),
    )
    action = forms.ChoiceField(label="对象操作")
    reason = forms.CharField(
        label="处理原因",
        max_length=MAX_MODERATION_REASON_LENGTH,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        actions = (ModerationAction.NO_ACTION, *SUBJECT_ACTIONS.get(report.subject_type, ()))
        self.fields["action"].choices = [
            (action, ModerationAction(action).label) for action in actions
        ]

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        action = cleaned_data.get("action")
        if status == ReportStatus.REJECTED and action != ModerationAction.NO_ACTION:
            self.add_error("action", "驳回举报时只能选择不处置。")
        elif status == ReportStatus.RESOLVED and action == ModerationAction.NO_ACTION:
            self.add_error("action", "确认举报成立时必须选择一个对象操作。")
        return cleaned_data
