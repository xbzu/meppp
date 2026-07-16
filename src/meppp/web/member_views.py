from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods, require_POST

from meppp.accounts.member_forms import ProfileSettingsForm, StyledPasswordChangeForm
from meppp.accounts.member_services import update_member_profile
from meppp.accounts.models import Profile
from meppp.audit.services import record_event
from meppp.publishing.member_services import withdraw_comment, withdraw_entry
from meppp.publishing.models import Comment, ContentState, Entry

from .rate_limit import RateLimitExceeded, enforce_rate_limit


def _rate_limited(request, error: RateLimitExceeded):
    response = render(request, "web/429.html", status=429)
    response.headers["Retry-After"] = str(error.retry_after)
    return response


@login_required
def dashboard(request):
    entries = Entry.objects.filter(author=request.user).prefetch_related("topics")
    comments = Comment.objects.filter(author=request.user).select_related("entry")
    entry_page = Paginator(entries, 12).get_page(request.GET.get("entries"))
    comment_page = Paginator(comments, 12).get_page(request.GET.get("comments"))
    states = (
        ContentState.PENDING,
        ContentState.PUBLISHED,
        ContentState.HIDDEN,
        ContentState.DELETED,
    )
    entry_counts = entries.aggregate(
        **{state: Count("pk", filter=Q(state=state)) for state in states}
    )
    comment_counts = comments.aggregate(
        **{state: Count("pk", filter=Q(state=state)) for state in states}
    )
    state_counts = {state: entry_counts[state] + comment_counts[state] for state in states}
    return render(
        request,
        "web/member_dashboard.html",
        {
            "entry_page": entry_page,
            "comment_page": comment_page,
            "state_counts": state_counts,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def settings(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = ProfileSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            update_member_profile(
                member=request.user,
                display_name=form.cleaned_data["display_name"],
                bio=form.cleaned_data["bio"],
            )
            messages.success(request, "公开资料已更新。")
            return redirect("web:member-settings")
    else:
        form = ProfileSettingsForm(instance=profile)
    return render(request, "web/member_settings.html", {"form": form})


class MemberPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    form_class = StyledPasswordChangeForm
    template_name = "web/member_password.html"
    success_url = reverse_lazy("web:member-settings")

    def post(self, request, *args, **kwargs):
        try:
            enforce_rate_limit(
                request,
                scope="account_security",
                identity=str(request.user.public_id),
            )
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            update_session_auth_hash(self.request, user)
            record_event(
                actor=user,
                action="account.password.changed",
                target_type="user",
                target_public_id=user.public_id,
                metadata={"schema_version": 1},
            )
        messages.success(self.request, "密码已更新，当前设备仍保持登录。")
        return redirect(self.success_url)


def _show_validation_error(request, error: ValidationError) -> None:
    for message in error.messages:
        messages.error(request, message)


@login_required
@require_POST
def entry_withdraw(request, public_id):
    try:
        withdraw_entry(actor=request.user, entry_public_id=public_id)
    except ObjectDoesNotExist as error:
        raise Http404 from error
    except ValidationError as error:
        _show_validation_error(request, error)
    else:
        messages.success(request, "内容已撤回，不再公开展示。")
    return redirect("web:member-dashboard")


@login_required
@require_POST
def comment_withdraw(request, public_id):
    try:
        withdraw_comment(actor=request.user, comment_public_id=public_id)
    except ObjectDoesNotExist as error:
        raise Http404 from error
    except ValidationError as error:
        _show_validation_error(request, error)
    else:
        messages.success(request, "评论已撤回，不再公开展示。")
    return redirect("web:member-dashboard")
