from datetime import timedelta

from django import forms
from django.utils import timezone

from .services import MAX_INVITATION_REASON_LENGTH


class InvitationIssueForm(forms.Form):
    bound_email = forms.EmailField(
        label="绑定邮箱",
        required=False,
        help_text="可选。填写后，只有使用同一邮箱注册的成员才能领取。",
    )
    expires_at = forms.DateTimeField(
        label="有效期至",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="到期后邀请码自动失效。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial_expiration = timezone.localtime(timezone.now() + timedelta(days=7))
        self.fields["expires_at"].initial = initial_expiration.replace(second=0, microsecond=0)

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at <= timezone.now():
            raise forms.ValidationError("有效期必须是未来时间。")
        return expires_at

    def clean_bound_email(self) -> str:
        return self.cleaned_data.get("bound_email", "").strip().lower()


class InvitationRevokeForm(forms.Form):
    reason = forms.CharField(
        label="撤销原因",
        required=False,
        max_length=MAX_INVITATION_REASON_LENGTH,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="原因会进入不可修改的审计记录。",
    )

    def clean_reason(self) -> str:
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("撤销原因不能为空。")
        return reason
