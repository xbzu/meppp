from __future__ import annotations

from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from .models import Profile


class StyledFormMixin:
    def apply_field_styles(self) -> None:
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            field.widget.attrs.setdefault("class", "field-control")


class ProfileSettingsForm(StyledFormMixin, forms.ModelForm):
    avatar_upload = forms.FileField(
        label="上传新头像",
        required=False,
        help_text="支持 JPG、PNG、WebP；最大 5 MB。保存时会安全处理为方形 WebP。",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "data-avatar-input": "",
            }
        ),
    )
    remove_avatar = forms.BooleanField(
        label="删除现有头像",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
    )

    class Meta:
        model = Profile
        fields = ("display_name", "bio")
        labels = {"display_name": "显示名称", "bio": "个人简介"}
        help_texts = {
            "display_name": "留空时公开显示用户名。",
            "bio": "最多 500 个字符；不会公开邮箱或登录信息。",
        }
        widgets = {
            "display_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "bio": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, configuration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        if configuration is not None and not configuration.avatar_uploads_enabled:
            self.fields.pop("avatar_upload")
        if not self.instance.avatar:
            self.fields.pop("remove_avatar")
        self.order_fields(("avatar_upload", "remove_avatar", "display_name", "bio"))
        self.apply_field_styles()

    def clean_display_name(self) -> str:
        return self.cleaned_data["display_name"].strip()

    def clean_bio(self) -> str:
        return self.cleaned_data["bio"].strip()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("avatar_upload") and cleaned_data.get("remove_avatar"):
            raise forms.ValidationError("上传新头像和删除头像不能同时选择。")
        return cleaned_data


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()
        self.fields["old_password"].label = "当前密码"
        self.fields["new_password1"].label = "新密码"
        self.fields["new_password2"].label = "再次输入新密码"
