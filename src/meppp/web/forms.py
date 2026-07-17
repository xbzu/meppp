from __future__ import annotations

import json

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password

from meppp.accounts.models import User
from meppp.accounts.normalization import normalize_username
from meppp.configuration.models import (
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_IMAGES_PER_POST,
    RegistrationMode,
)
from meppp.external.parsing import (
    PROVIDER_X,
    PROVIDER_YOUTUBE,
    ExternalURLValidationError,
    parse_external_url,
)
from meppp.moderation.models import ReportReason
from meppp.publishing.models import MAX_VIDEO_UPLOAD_BYTES, Topic


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
        required=True,
        help_text="必填，用于账号恢复时核对；不会展示在公开资料中。",
    )
    invitation_token = forms.CharField(
        label="邀请码",
        max_length=200,
        help_text="请输入管理员单独发给你的一次性邀请码。",
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )
    accept_rules = forms.BooleanField(label="我愿意遵守社区公约")
    website = forms.CharField(
        required=False,
        label="个人网站",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "tabindex": "-1",
                "aria-hidden": "true",
            }
        ),
    )

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
        self.fields[
            "password1"
        ].help_text = "至少 8 个字符；不能与用户名或邮箱过于相似，也不能使用常见密码或纯数字。"
        self.fields["password2"].help_text = "请再次输入同一密码。"
        self.fields["password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["password2"].widget.attrs["autocomplete"] = "new-password"
        self.order_fields(
            (
                "username",
                "email",
                "password1",
                "password2",
                "invitation_token",
                "accept_rules",
                "website",
            )
        )

    def clean_username(self) -> str:
        username = normalize_username(self.cleaned_data["username"])
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("这个用户名已经被使用。")
        return username

    def clean_email(self) -> str:
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("这个邮箱已经被使用。")
        return email

    def clean_website(self) -> str:
        if self.cleaned_data.get("website", "").strip():
            raise forms.ValidationError("无法完成注册，请刷新页面后重试。")
        return ""


class AccountRecoveryForm(FormStyleMixin, forms.Form):
    username = forms.CharField(label="用户名", max_length=150)
    email = forms.EmailField(label="注册邮箱")
    recovery_code = forms.CharField(
        label="账号恢复码",
        max_length=200,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        help_text="输入注册或上次恢复后显示的一次性恢复码。",
    )
    password1 = forms.CharField(
        label="新密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="确认新密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_username(self) -> str:
        return normalize_username(self.cleaned_data["username"])

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        first = cleaned_data.get("password1")
        second = cleaned_data.get("password2")
        if first and second and first != second:
            self.add_error("password2", "两次输入的密码不一致。")
        elif second:
            try:
                validate_password(second)
            except forms.ValidationError as error:
                self.add_error("password2", error)
        return cleaned_data


class RecoveryCodeRotateForm(FormStyleMixin, forms.Form):
    current_password = forms.CharField(
        label="当前密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()


class EntryForm(FormStyleMixin, forms.Form):
    body = forms.CharField(
        label="正文",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "写下一个值得留下来的想法……",
                "data-character-input": "entry",
            }
        ),
    )
    source_url = forms.URLField(
        label="分享 X / YouTube 来源",
        required=False,
        max_length=2_048,
        widget=forms.URLInput(
            attrs={
                "placeholder": "粘贴 X Post 或 YouTube 视频的公开链接",
                "autocomplete": "off",
                "spellcheck": "false",
                "inputmode": "url",
                "data-source-url": "",
            }
        ),
        help_text=("发布后会生成带原始署名和链接的来源卡片；不会下载、转存或冒充原作者的媒体。"),
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
    video = forms.FileField(
        label="视频",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "video/mp4,video/webm,.mp4,.webm",
                "class": "sr-only video-input",
                "data-video-input": "",
                "data-max-bytes": str(MAX_VIDEO_UPLOAD_BYTES),
                "aria-describedby": "video-help video-status",
            }
        ),
    )
    image_alt_texts = forms.CharField(required=False, widget=forms.HiddenInput())
    nonce = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, configuration, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        self.external_source = None
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
        if self.maximum_images == 0:
            self.fields.pop("images")
            self.fields.pop("image_alt_texts")
        if not configuration.video_uploads_enabled:
            self.fields.pop("video")
        if not configuration.x_references_enabled and not configuration.youtube_references_enabled:
            self.fields.pop("source_url")
        elif configuration.x_references_enabled and not configuration.youtube_references_enabled:
            self.fields["source_url"].label = "分享 X 来源"
            self.fields["source_url"].widget.attrs["placeholder"] = "粘贴 X Post 的公开链接"
            self.fields[
                "source_url"
            ].help_text = "发布后会生成带原始署名和链接的 X 来源卡片；不会下载或转存原媒体。"
        elif configuration.youtube_references_enabled and not configuration.x_references_enabled:
            self.fields["source_url"].label = "分享 YouTube 来源"
            self.fields["source_url"].widget.attrs["placeholder"] = "粘贴 YouTube 视频的公开链接"
            self.fields[
                "source_url"
            ].help_text = "发布后会生成带原始署名和链接的 YouTube 来源卡片；不会下载或转存原媒体。"
        self.order_fields(
            (
                "body",
                "images",
                "image_alt_texts",
                "video",
                "source_url",
                "topics",
                "nonce",
            )
        )
        self.apply_field_styles()

    def clean_body(self) -> str:
        return self.cleaned_data.get("body", "").strip()

    def clean_source_url(self) -> str:
        source_url = self.cleaned_data.get("source_url", "").strip()
        if not source_url:
            self.external_source = None
            return ""
        try:
            self.external_source = parse_external_url(source_url)
        except ExternalURLValidationError as error:
            raise forms.ValidationError(str(error)) from error
        if (
            self.external_source.provider == PROVIDER_X
            and not self.configuration.x_references_enabled
        ):
            raise forms.ValidationError("管理员当前关闭了 X 来源分享。")
        if (
            self.external_source.provider == PROVIDER_YOUTUBE
            and not self.configuration.youtube_references_enabled
        ):
            raise forms.ValidationError("管理员当前关闭了 YouTube 来源分享。")
        return self.external_source.canonical_url

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

    def clean_video(self):
        video = self.cleaned_data.get("video")
        if video is None:
            return None
        if video.size <= 0:
            raise forms.ValidationError("视频文件不能为空。")
        if video.size > MAX_VIDEO_UPLOAD_BYTES:
            raise forms.ValidationError("视频不能超过 20 MB。")
        return video

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
        video = cleaned_data.get("video")
        source_url = cleaned_data.get("source_url", "")
        if len(alt_texts) > len(images):
            self.add_error("images", "图片与说明数量不一致，请重新选择图片。")
        cleaned_data["image_alt_texts"] = alt_texts + [""] * max(
            0,
            len(images) - len(alt_texts),
        )
        if images and video:
            self.add_error("video", "一条动态请选择图片或视频，不要同时上传两种媒体。")
        if source_url and (images or video):
            self.add_error(
                "source_url",
                "分享外部来源时不能同时上传本地图片或视频，请分成两条动态。",
            )
        if not cleaned_data.get("body") and not source_url and not images and video is None:
            self.add_error("body", "请填写正文，或添加图片、视频、X / YouTube 来源。")
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
