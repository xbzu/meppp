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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()

    def clean_display_name(self) -> str:
        return self.cleaned_data["display_name"].strip()

    def clean_bio(self) -> str:
        return self.cleaned_data["bio"].strip()


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_field_styles()
        self.fields["old_password"].label = "当前密码"
        self.fields["new_password1"].label = "新密码"
        self.fields["new_password2"].label = "再次输入新密码"
