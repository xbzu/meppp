from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from meppp.accounts.models import User
from meppp.accounts.normalization import normalize_username
from meppp.moderation.models import ReportReason
from meppp.publishing.models import Topic


class FormStyleMixin:
    def apply_field_styles(self) -> None:
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "check-input")
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                widget.attrs.setdefault("class", "choice-grid")
            elif isinstance(widget, forms.HiddenInput):
                continue
            else:
                widget.attrs.setdefault("class", "field-control")


class MemberAuthenticationForm(FormStyleMixin, AuthenticationForm):
    username = forms.CharField(label="用户名", max_length=150, widget=forms.TextInput())
    password = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
        self.fields["password"].widget.attrs["autocomplete"] = "current-password"


class RegistrationForm(FormStyleMixin, UserCreationForm):
    email = forms.EmailField(
        label="邮箱",
        required=False,
        help_text="可选。首版暂不提供邮箱登录或密码找回。",
    )
    accept_rules = forms.BooleanField(label="我愿意遵守社区公约")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()
        self.fields["username"].label = "用户名"
        self.fields["username"].help_text = "以后用于登录；不区分大小写。"
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["password1"].label = "密码"
        self.fields["password2"].label = "确认密码"
        self.fields["password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["password2"].widget.attrs["autocomplete"] = "new-password"

    def clean_username(self) -> str:
        username = normalize_username(self.cleaned_data["username"])
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("这个用户名已经被使用。")
        return username

    def clean_email(self) -> str:
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("这个邮箱已经被使用。")
        return email


class EntryForm(FormStyleMixin, forms.Form):
    body = forms.CharField(
        label="正文",
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "placeholder": "写下一个值得留下来的想法……",
                "data-character-input": "entry",
            }
        ),
    )
    topics = forms.ModelMultipleChoiceField(
        label="话题",
        queryset=Topic.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="可选，最多 3 个。",
    )
    nonce = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        self.fields["body"].max_length = configuration.post_max_length
        self.fields["body"].widget.attrs["maxlength"] = configuration.post_max_length
        self.fields["body"].help_text = f"最多 {configuration.post_max_length} 个字符。"
        self.fields["topics"].queryset = Topic.objects.order_by("label")
        self.apply_field_styles()

    def clean_body(self) -> str:
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("正文不能为空。")
        return body

    def clean_topics(self):
        topics = self.cleaned_data["topics"]
        if len(topics) > 3:
            raise forms.ValidationError("每条内容最多选择 3 个话题。")
        return topics


class CommentForm(FormStyleMixin, forms.Form):
    body = forms.CharField(
        label="写下评论",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "把话说清楚，也给彼此留一点余地。",
                "data-character-input": "comment",
            }
        ),
    )
    nonce = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].max_length = configuration.comment_max_length
        self.fields["body"].widget.attrs["maxlength"] = configuration.comment_max_length
        self.fields["body"].help_text = f"最多 {configuration.comment_max_length} 个字符。"
        self.apply_field_styles()

    def clean_body(self) -> str:
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("评论不能为空。")
        return body


class DesiredStateForm(forms.Form):
    state = forms.ChoiceField(choices=(("on", "开启"), ("off", "关闭")))
    next = forms.CharField(required=False, widget=forms.HiddenInput())

    @property
    def desired_state(self) -> bool:
        return self.cleaned_data["state"] == "on"


class ReportForm(FormStyleMixin, forms.Form):
    reason = forms.ChoiceField(label="举报原因", choices=ReportReason.choices)
    details = forms.CharField(
        label="补充说明",
        required=False,
        max_length=1_000,
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": "请只填写帮助管理员判断的必要信息。"}
        ),
    )
    next = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()

    def clean(self):
        cleaned_data = super().clean()
        details = cleaned_data.get("details", "").strip()
        cleaned_data["details"] = details
        if cleaned_data.get("reason") == ReportReason.OTHER and not details:
            self.add_error("details", "选择其他原因时，请补充说明。")
        return cleaned_data
