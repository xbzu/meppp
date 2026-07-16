from django import forms

from .models import ContentReviewOutcome
from .services import MAX_CONTENT_REVIEW_REASON_LENGTH


class ContentReviewForm(forms.Form):
    outcome = forms.ChoiceField(
        label="审核结论",
        choices=ContentReviewOutcome.choices,
        widget=forms.RadioSelect,
        help_text="批准后立即公开；驳回后保持隐藏。两种结论都会通知作者。",
    )
    reason = forms.CharField(
        label="审核理由",
        max_length=MAX_CONTENT_REVIEW_REASON_LENGTH,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="请写清判断依据；该理由会进入审计记录并通知作者。",
    )
    confirm = forms.BooleanField(
        label="我已核对内容、作者和审核结论",
        error_messages={"required": "提交前请确认已经核对审核对象和结论。"},
    )
