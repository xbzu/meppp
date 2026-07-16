from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from meppp.accounts.normalization import username_identity
from meppp.accounts.services import register_member
from meppp.configuration.models import RegistrationMode
from meppp.configuration.selectors import get_site_configuration
from meppp.moderation.models import SubjectType
from meppp.moderation.services import submit_report
from meppp.notifications.models import Notification
from meppp.notifications.services import mark_all_read
from meppp.publishing.models import Comment, ContentState
from meppp.publishing.selectors import active_topics, public_comments, public_entries
from meppp.social.models import Follow
from meppp.social.services import set_entry_like, set_follow

from .forms import (
    CommentForm,
    DesiredStateForm,
    EntryForm,
    MemberAuthenticationForm,
    RegistrationForm,
    ReportForm,
)
from .nonces import consume_nonce, issue_nonce, nonce_is_issued
from .rate_limit import RateLimitExceeded, enforce_rate_limit
from .services import DuplicateSubmission, add_comment_once, publish_entry_once


def _safe_next(request, value: str | None, *, fallback: str) -> str:
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return fallback


def _show_validation_error(request, error: ValidationError) -> None:
    for error_message in error.messages:
        messages.error(request, error_message)


def _rate_limited(request, error: RateLimitExceeded):
    response = render(request, "web/429.html", status=429)
    response.headers["Retry-After"] = str(error.retry_after)
    return response


@method_decorator(never_cache, name="dispatch")
class MemberLoginView(LoginView):
    authentication_form = MemberAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        identity = username_identity(request.POST.get("username", ""))
        try:
            enforce_rate_limit(request, scope="login", identity=identity)
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        return super().post(request, *args, **kwargs)


class MemberLogoutView(LogoutView):
    next_page = "/"


@sensitive_post_parameters("password1", "password2", "invitation_token")
@never_cache
@require_http_methods(["GET", "POST"])
def register(request):
    configuration = get_site_configuration()
    registration_available = configuration.registration_mode in {
        RegistrationMode.OPEN,
        RegistrationMode.INVITE,
    }
    next_value = request.POST.get("next") or request.GET.get("next", "")

    if request.method == "POST":
        try:
            enforce_rate_limit(
                request,
                scope="register",
                identity=username_identity(request.POST.get("username", "")),
            )
        except RateLimitExceeded as error:
            return _rate_limited(request, error)

        if not registration_available:
            return render(
                request,
                "registration/register.html",
                {
                    "form": None,
                    "registration_available": False,
                    "registration_mode": configuration.registration_mode,
                    "next": next_value,
                },
                status=403,
            )

        form = RegistrationForm(
            request.POST,
            registration_mode=configuration.registration_mode,
        )
        if form.is_valid():
            try:
                user = register_member(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password1"],
                    invitation_token=form.cleaned_data.get("invitation_token", ""),
                )
            except IntegrityError:
                form.add_error(None, "账号创建发生冲突，请重新选择用户名。")
            except ValidationError as error:
                form.add_error(None, error)
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, "欢迎加入。你的社区档案已经建立。")
                return redirect(
                    _safe_next(
                        request,
                        next_value,
                        fallback=reverse("web:home"),
                    )
                )
    else:
        form = (
            RegistrationForm(registration_mode=configuration.registration_mode)
            if registration_available
            else None
        )

    return render(
        request,
        "registration/register.html",
        {
            "form": form,
            "registration_available": registration_available,
            "registration_mode": configuration.registration_mode,
            "next": next_value,
        },
    )


def home(request):
    entries = public_entries(viewer=request.user)
    mode = request.GET.get("feed", "latest")
    query = request.GET.get("q", "").strip()[:80]
    topic_slug = request.GET.get("topic", "").strip()[:80]

    if mode == "following" and request.user.is_authenticated:
        entries = entries.filter(author__follower_links__follower=request.user)
    else:
        mode = "latest"
    if query:
        entries = entries.filter(
            Q(body__icontains=query)
            | Q(author__username__icontains=query)
            | Q(author__profile__display_name__icontains=query)
        ).distinct()
    if topic_slug:
        entries = entries.filter(topics__slug=topic_slug).distinct()

    paginator = Paginator(entries, 12)
    page = paginator.get_page(request.GET.get("page"))
    query_parameters = request.GET.copy()
    query_parameters.pop("page", None)
    return render(
        request,
        "web/home.html",
        {
            "page": page,
            "feed_mode": mode,
            "query": query,
            "topic_slug": topic_slug,
            "topics": active_topics(),
            "query_string": query_parameters.urlencode(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def entry_create(request):
    configuration = get_site_configuration()
    purpose = "entry:create"
    if request.method == "POST":
        try:
            enforce_rate_limit(
                request,
                scope="publish",
                identity=str(request.user.public_id),
            )
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        form = EntryForm(request.POST, configuration=configuration)
        if form.is_valid():
            token = form.cleaned_data["nonce"]
            if not nonce_is_issued(request, purpose=purpose, token=token):
                form.add_error(None, "这个发布表单已经处理过，请刷新页面后再试。")
            else:
                try:
                    entry = publish_entry_once(
                        author=request.user,
                        body=form.cleaned_data["body"],
                        topics=form.cleaned_data["topics"],
                        purpose=purpose,
                        token=token,
                    )
                except DuplicateSubmission:
                    form.add_error(None, "这个发布表单已经处理过，请刷新页面后再试。")
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    consume_nonce(request, purpose=purpose, token=token)
                    if entry.state == ContentState.PENDING:
                        messages.success(request, "内容已提交审核，通过后会出现在公开信息流。")
                        return redirect("web:home")
                    messages.success(request, "内容已经发布。")
                    return redirect("web:entry-detail", public_id=entry.public_id)
    else:
        form = EntryForm(
            configuration=configuration,
            initial={"nonce": issue_nonce(request, purpose=purpose)},
        )
    return render(request, "web/entry_form.html", {"form": form})


def entry_detail(request, public_id):
    entry = get_object_or_404(public_entries(viewer=request.user), public_id=public_id)
    comments = public_comments(entry=entry)
    comment_form = None
    if request.user.is_authenticated:
        purpose = f"entry:{entry.public_id}:comment"
        comment_form = CommentForm(
            configuration=get_site_configuration(),
            initial={"nonce": issue_nonce(request, purpose=purpose)},
        )
    return render(
        request,
        "web/entry_detail.html",
        {
            "entry": entry,
            "comments": comments,
            "comment_form": comment_form,
        },
    )


@login_required
@require_POST
def comment_create(request, public_id):
    configuration = get_site_configuration()
    form = CommentForm(request.POST, configuration=configuration)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect("web:entry-detail", public_id=public_id)

    purpose = f"entry:{public_id}:comment"
    token = form.cleaned_data["nonce"]
    if not nonce_is_issued(request, purpose=purpose, token=token):
        messages.info(request, "这条评论已经处理过。")
        return redirect("web:entry-detail", public_id=public_id)
    try:
        enforce_rate_limit(
            request,
            scope="comment",
            identity=str(request.user.public_id),
        )
        comment = add_comment_once(
            author=request.user,
            entry_public_id=public_id,
            body=form.cleaned_data["body"],
            purpose=purpose,
            token=token,
        )
    except RateLimitExceeded as error:
        return _rate_limited(request, error)
    except DuplicateSubmission:
        messages.info(request, "这条评论已经处理过。")
        return redirect("web:entry-detail", public_id=public_id)
    except ValidationError as error:
        _show_validation_error(request, error)
    else:
        consume_nonce(request, purpose=purpose, token=token)
        if comment.state == ContentState.PENDING:
            messages.success(request, "评论已提交审核。")
        else:
            messages.success(request, "评论已经发布。")
    return redirect(f"{reverse('web:entry-detail', kwargs={'public_id': public_id})}#comments")


@login_required
@require_POST
def entry_like(request, public_id):
    form = DesiredStateForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("invalid state")
    try:
        enforce_rate_limit(
            request,
            scope="reaction",
            identity=str(request.user.public_id),
        )
        set_entry_like(
            actor=request.user,
            entry_public_id=public_id,
            liked=form.desired_state,
        )
    except RateLimitExceeded as error:
        return _rate_limited(request, error)
    except ValidationError as error:
        _show_validation_error(request, error)
    fallback = reverse("web:entry-detail", kwargs={"public_id": public_id})
    return redirect(_safe_next(request, form.cleaned_data.get("next"), fallback=fallback))


def member_profile(request, public_id):
    user_model = get_user_model()
    member = get_object_or_404(
        user_model.objects.filter(public_id=public_id, is_active=True)
        .select_related("profile")
        .annotate(
            public_entry_count=Count(
                "entries",
                filter=Q(entries__state=ContentState.PUBLISHED),
                distinct=True,
            ),
            follower_count=Count("follower_links", distinct=True),
            following_count=Count("following_links", distinct=True),
        )
    )
    entries = public_entries(viewer=request.user).filter(author=member)
    paginator = Paginator(entries, 12)
    page = paginator.get_page(request.GET.get("page"))
    is_following = False
    if request.user.is_authenticated and request.user.pk != member.pk:
        is_following = Follow.objects.filter(follower=request.user, followed=member).exists()
    return render(
        request,
        "web/profile.html",
        {"member": member, "page": page, "is_following": is_following},
    )


@login_required
@require_POST
def member_follow(request, public_id):
    form = DesiredStateForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("invalid state")
    try:
        enforce_rate_limit(
            request,
            scope="reaction",
            identity=str(request.user.public_id),
        )
        set_follow(
            actor=request.user,
            member_public_id=public_id,
            following=form.desired_state,
        )
    except RateLimitExceeded as error:
        return _rate_limited(request, error)
    except ValidationError as error:
        _show_validation_error(request, error)
    fallback = reverse("web:member-profile", kwargs={"public_id": public_id})
    return redirect(_safe_next(request, form.cleaned_data.get("next"), fallback=fallback))


def _report_target_or_404(*, subject_type: str, public_id):
    if subject_type == SubjectType.USER:
        return get_object_or_404(get_user_model(), public_id=public_id, is_active=True)
    if subject_type == SubjectType.ENTRY:
        return get_object_or_404(public_entries(), public_id=public_id)
    if subject_type == SubjectType.COMMENT:
        return get_object_or_404(
            Comment.objects.select_related("entry", "author").filter(
                public_id=public_id,
                state=ContentState.PUBLISHED,
                author__is_active=True,
                entry__state=ContentState.PUBLISHED,
                entry__author__is_active=True,
            )
        )
    raise Http404


def _report_fallback(subject_type: str, target) -> str:
    if subject_type == SubjectType.USER:
        return reverse("web:member-profile", kwargs={"public_id": target.public_id})
    if subject_type == SubjectType.ENTRY:
        return reverse("web:entry-detail", kwargs={"public_id": target.public_id})
    return reverse("web:entry-detail", kwargs={"public_id": target.entry.public_id})


@login_required
@require_http_methods(["GET", "POST"])
def report_create(request, public_id, *, subject_type: str):
    target = _report_target_or_404(subject_type=subject_type, public_id=public_id)
    target_owner_id = target.pk if subject_type == SubjectType.USER else target.author_id
    if target_owner_id == request.user.pk:
        raise Http404
    fallback = _report_fallback(subject_type, target)
    return_url = _safe_next(
        request,
        request.POST.get("next") or request.GET.get("next"),
        fallback=fallback,
    )

    if request.method == "POST":
        try:
            enforce_rate_limit(
                request,
                scope="report",
                identity=str(request.user.public_id),
            )
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        form_data = request.POST.copy()
        form_data["next"] = return_url
        form = ReportForm(form_data)
        if form.is_valid():
            try:
                _, created = submit_report(
                    reporter=request.user,
                    subject_type=subject_type,
                    subject_public_id=public_id,
                    reason=form.cleaned_data["reason"],
                    details=form.cleaned_data["details"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if created:
                    messages.success(request, "举报已提交，管理员会在后台处理。")
                else:
                    messages.info(request, "你已经提交过这项举报。")
                return redirect(
                    _safe_next(
                        request,
                        form.cleaned_data.get("next"),
                        fallback=fallback,
                    )
                )
    else:
        form = ReportForm(initial={"next": return_url})
    return render(
        request,
        "web/report_form.html",
        {
            "form": form,
            "return_url": return_url,
            "target": target,
            "subject_type": subject_type,
        },
    )


@login_required
def notification_list(request):
    notifications = Notification.objects.filter(recipient=request.user).select_related("actor")[:50]
    return render(request, "web/notifications.html", {"notifications": notifications})


def not_found(request, exception):
    return render(request, "web/404.html", status=404)


@login_required
@require_POST
def notifications_read(request):
    try:
        enforce_rate_limit(
            request,
            scope="reaction",
            identity=str(request.user.public_id),
        )
    except RateLimitExceeded as error:
        return _rate_limited(request, error)
    mark_all_read(recipient=request.user)
    messages.success(request, "通知已全部标为已读。")
    return redirect("web:notifications")
