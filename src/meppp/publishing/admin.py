from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .forms import ContentReviewForm
from .models import (
    Comment,
    ContentReviewDecision,
    ContentReviewOutcome,
    ContentState,
    Entry,
    EntryTopic,
    PendingComment,
    PendingEntry,
    Topic,
)
from .services import review_comment, review_entry


class ReadOnlyContentAdminMixin:
    change_form_template = "admin/publishing/read_only_change_form.html"
    actions = ()
    pending_admin_model_name = ""

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.concrete_fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method != "GET":
            raise PermissionDenied("请使用专用审核入口变更内容状态")
        obj = self.get_object(request, unquote(object_id))
        content_review_url = None
        if (
            obj is not None
            and obj.state == ContentState.PENDING
            and self.has_change_permission(request, obj)
        ):
            content_review_url = reverse(
                f"admin:publishing_{self.pending_admin_model_name}_review",
                args=[obj.pk],
            )
        extra_context = {
            **(extra_context or {}),
            "content_review_url": content_review_url,
            "show_save": False,
            "show_save_and_continue": False,
            "show_save_and_add_another": False,
            "show_delete": False,
        }
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.display(description="状态", ordering="state")
    def state_display(self, obj):
        return obj.get_state_display()

    @admin.display(description="内容摘要", ordering="body")
    def body_summary(self, obj):
        return obj.body[:100]


@admin.register(Entry)
class EntryAdmin(ReadOnlyContentAdminMixin, admin.ModelAdmin):
    pending_admin_model_name = "pendingentry"
    list_display = ("body_summary", "author", "state_display", "created_at", "updated_at")
    list_filter = ("state", "created_at")
    search_fields = ("body", "author__username")


@admin.register(Comment)
class CommentAdmin(ReadOnlyContentAdminMixin, admin.ModelAdmin):
    pending_admin_model_name = "pendingcomment"
    list_display = ("body_summary", "entry", "author", "state_display", "created_at")
    list_filter = ("state", "created_at")
    search_fields = ("body", "author__username", "entry__body")


class PendingReviewAdminMixin:
    actions = ()
    list_display_links = None
    list_filter = ("created_at",)
    ordering = ("created_at", "pk")
    change_list_template = "admin/publishing/pending_change_list.html"
    review_template = "admin/publishing/content_review.html"
    target_permission = ""
    target_label = "内容"

    def get_queryset(self, request):
        return super().get_queryset(request).filter(state=ContentState.PENDING)

    def _can_review(self, request) -> bool:
        return bool(
            request.user.is_active
            and request.user.is_staff
            and request.user.has_perm(self.target_permission)
        )

    def has_module_permission(self, request):
        return self._can_review(request)

    def has_view_permission(self, request, obj=None):
        return self._can_review(request)

    def has_change_permission(self, request, obj=None):
        return self._can_review(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_model_perms(self, request):
        can_review = self._can_review(request)
        return {"add": False, "change": can_review, "delete": False, "view": can_review}

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                "<path:object_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name=f"{opts.app_label}_{opts.model_name}_review",
            )
        ]
        return custom_urls + super().get_urls()

    def review_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404("Pending content not found")

        form = ContentReviewForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                self.perform_review(
                    target=target,
                    actor=request.user,
                    outcome=form.cleaned_data["outcome"],
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                outcome_label = ContentReviewOutcome(form.cleaned_data["outcome"]).label
                self.message_user(
                    request,
                    f"{self.target_label}已完成审核：{outcome_label}。决定已记录并通知作者。",
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(
                    reverse(
                        f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
                    )
                )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": target,
            "title": f"审核待审{self.target_label}",
            "target": target,
            "target_label": self.target_label,
            "form": form,
            "media": self.media + form.media,
            "queue_url": reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"
            ),
            "is_comment": isinstance(target, Comment),
        }
        return TemplateResponse(request, self.review_template, context)

    @admin.display(description="作者")
    def author_identity(self, obj):
        return f"@{obj.author.username}"

    @admin.display(description="待审正文", ordering="body")
    def pending_summary(self, obj):
        return obj.body[:120]

    @admin.display(description="提交时间", ordering="created_at")
    def submitted_at(self, obj):
        return obj.created_at

    @admin.display(description="操作")
    def review_action(self, obj):
        url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_review",
            args=[obj.pk],
        )
        return format_html('<a class="button" href="{}">立即审核</a>', url)

    def perform_review(self, *, target, actor, outcome, reason):
        raise NotImplementedError


@admin.register(PendingEntry)
class PendingEntryAdmin(PendingReviewAdminMixin, admin.ModelAdmin):
    list_display = ("pending_summary", "author_identity", "submitted_at", "review_action")
    search_fields = ("body", "author__username")
    target_permission = "publishing.change_entry"
    target_label = "内容"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("author")

    def perform_review(self, *, target, actor, outcome, reason):
        return review_entry(entry=target, actor=actor, outcome=outcome, reason=reason)


@admin.register(PendingComment)
class PendingCommentAdmin(PendingReviewAdminMixin, admin.ModelAdmin):
    list_display = (
        "pending_summary",
        "entry_summary",
        "author_identity",
        "submitted_at",
        "review_action",
    )
    search_fields = ("body", "author__username", "entry__body")
    target_permission = "publishing.change_comment"
    target_label = "评论"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("author", "entry", "entry__author")

    @admin.display(description="原内容")
    def entry_summary(self, obj):
        return obj.entry.body[:70]

    def perform_review(self, *, target, actor, outcome, reason):
        return review_comment(comment=target, actor=actor, outcome=outcome, reason=reason)


@admin.register(ContentReviewDecision)
class ContentReviewDecisionAdmin(admin.ModelAdmin):
    list_display = ("target_kind", "target_summary", "outcome", "actor", "created_at")
    list_filter = ("outcome", "created_at")
    search_fields = ("entry__body", "comment__body", "actor__username", "reason")
    readonly_fields = (
        "public_id",
        "entry",
        "comment",
        "actor",
        "outcome",
        "reason",
        "before_state",
        "after_state",
        "created_at",
    )
    actions = ()

    @admin.display(description="类型")
    def target_kind(self, obj):
        return "内容" if obj.entry_id is not None else "评论"

    @admin.display(description="审核对象")
    def target_summary(self, obj):
        target = obj.target
        return target.body[:100] if target is not None else "（对象不存在）"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("slug", "label", "created_at")
    search_fields = ("slug", "label")
    readonly_fields = ("public_id", "created_at", "updated_at")


admin.site.register(EntryTopic)
