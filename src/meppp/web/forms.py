from __future__ import annotations

import json

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from meppp.accounts.models import User
from meppp.accounts.normalization import normalize_username
from meppp.configuration.models import (
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_IMAGES_PER_POST,
    RegistrationMode,
)
from meppp.moderation.models import ReportReason
from meppp.publishing.models import Topic


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)]


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
    invitation_token = forms.CharField(
        label="邀请码",
        max_length=200,
        help_text="请输入管理员单独发给你的一次性邀请码。",
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )
    accept_rules = forms.BooleanField(label="我愿意遵守社区公约")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, registration_mode=RegistrationMode.OPEN, **kwargs):
        super().__init__(*args, **kwargs)
        if registration_mode != RegistrationMode.INVITE:
            self.fields.pop("invitation_token")
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
    images = MultipleImageField(
        label="配图",
        required=False,
        widget=MultipleImageInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "class": "sr-only image-input",
                "data-image-input": "",
                "aria-describedby": "image-help image-status",
            }
        ),
    )
    image_alt_texts = forms.CharField(required=False, widget=forms.HiddenInput())
    nonce = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        self.maximum_images = min(configuration.max_images_per_post, MAX_IMAGES_PER_POST)
        self.maximum_image_bytes = min(configuration.upload_max_bytes, MAX_IMAGE_UPLOAD_BYTES)
        self.fields["body"].max_length = configuration.post_max_length
        self.fields["body"].widget.attrs["maxlength"] = configuration.post_max_length
        self.fields["body"].help_text = f"最多 {configuration.post_max_length} 个字符。"
        self.fields["topics"].queryset = Topic.objects.order_by("label")
        self.fields["images"].widget.attrs.update(
            {
                "data-max-images": str(self.maximum_images),
                "data-max-bytes": str(self.maximum_image_bytes),
            }
        )
        self.order_fields(("body", "images", "image_alt_texts", "topics", "nonce"))
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

    def clean_images(self):
        images = self.cleaned_data["images"]
        maximum = self.maximum_images
        if len(images) > maximum:
            raise forms.ValidationError(f"每条内容最多上传 {maximum} 张图片。")
        if maximum == 0 and images:
            raise forms.ValidationError("管理员当前关闭了图片上传。")
        for image in images:
            if image.size <= 0:
                raise forms.ValidationError("图片文件不能为空。")
            if image.size > self.maximum_image_bytes:
                maximum_mb = self.maximum_image_bytes // (1024 * 1024)
                raise forms.ValidationError(f"每张图片不能超过 {maximum_mb} MB。")
        return images

    def clean_image_alt_texts(self):
        raw_value = self.cleaned_data.get("image_alt_texts", "")
        if not raw_value:
            return []
        try:
            values = json.loads(raw_value)
        except (TypeError, ValueError) as error:
            raise forms.ValidationError("图片说明格式无效，请重新选择图片。") from error
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise forms.ValidationError("图片说明格式无效，请重新选择图片。")
        if len(values) > self.maximum_images:
            raise forms.ValidationError("图片说明数量超过限制。")
        cleaned = [value.strip() for value in values]
        if any(len(value) > 240 for value in cleaned):
            raise forms.ValidationError("每条图片说明最多 240 个字符。")
        return cleaned

    def clean(self):
        cleaned_data = super().clean()
        images = cleaned_data.get("images", [])
        alt_texts = cleaned_data.get("image_alt_texts", [])
        if len(alt_texts) > len(images):
            self.add_error("images", "图片与说明数量不一致，请重新选择图片。")
        cleaned_data["image_alt_texts"] = alt_texts + [""] * max(
            0,
            len(images) - len(alt_texts),
        )
        return cleaned_data


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
