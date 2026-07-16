from __future__ import annotations

import logging
import os
import re
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from meppp.accounts.normalization import username_identity
from meppp.accounts.services import (
    issue_recovery_code,
    recover_account,
    register_member_with_recovery,
)
from meppp.configuration.models import RegistrationMode
from meppp.configuration.selectors import get_site_configuration
from meppp.external.models import ExternalReference
from meppp.external.services import refresh_external_reference
from meppp.moderation.models import SubjectType
from meppp.moderation.services import submit_report
from meppp.notifications.models import Notification
from meppp.notifications.services import mark_all_read
from meppp.publishing.image_processing import process_image_uploads
from meppp.publishing.models import (
    MAX_VIDEO_POSTER_BYTES,
    Attachment,
    Comment,
    ContentState,
    VideoAsset,
    VideoMimeType,
)
from meppp.publishing.selectors import active_topics, public_comments, public_entries
from meppp.publishing.services import preflight_publish_capacity
from meppp.publishing.video_processing import process_video_upload
from meppp.social.models import Follow
from meppp.social.services import set_entry_like, set_follow

from .forms import (
    AccountRecoveryForm,
    CommentForm,
    DesiredStateForm,
    EntryForm,
    MemberAuthenticationForm,
    RecoveryCodeRotateForm,
    RegistrationForm,
    ReportForm,
)
from .nonces import consume_nonce, issue_nonce, nonce_is_issued
from .rate_limit import RateLimitExceeded, client_ip, enforce_rate_limit
from .services import DuplicateSubmission, add_comment_once, publish_entry_once

RECOVERY_NOTICE_SESSION_KEY = "meppp.account_recovery_notice"
RECOVERY_NEXT_SESSION_KEY = "meppp.account_recovery_next"
RECOVERY_ISSUED_SESSION_KEY = "meppp.account_recovery_issued_at"
RECOVERY_NOTICE_TTL_SECONDS = 15 * 60
RECOVERY_CACHE_PREFIX = "meppp:account-recovery-notice:"
RECOVERY_NOTICE_CACHE = caches["recovery_notices"]
BYTE_RANGE_PATTERN = re.compile(
    r"bytes=(?P<start>[0-9]{0,20})-(?P<end>[0-9]{0,20})\Z",
    re.ASCII,
)
logger = logging.getLogger(__name__)


def _safe_next(request, value: str | None, *, fallback: str) -> str:
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return fallback


def _recovery_cache_key(reference: str) -> str:
    return f"{RECOVERY_CACHE_PREFIX}{reference}"


@sensitive_variables("recovery_code")
def _stage_recovery_code(request, *, recovery_code: str, next_url: str) -> None:
    previous_reference = request.session.get(RECOVERY_NOTICE_SESSION_KEY)
    if isinstance(previous_reference, str):
        RECOVERY_NOTICE_CACHE.delete(_recovery_cache_key(previous_reference))
    reference = secrets.token_urlsafe(24)
    RECOVERY_NOTICE_CACHE.set(
        _recovery_cache_key(reference),
        recovery_code,
        timeout=RECOVERY_NOTICE_TTL_SECONDS,
    )
    request.session[RECOVERY_NOTICE_SESSION_KEY] = reference
    request.session[RECOVERY_ISSUED_SESSION_KEY] = int(timezone.now().timestamp())
    request.session[RECOVERY_NEXT_SESSION_KEY] = next_url


def _show_validation_error(request, error: ValidationError) -> None:
    for error_message in error.messages:
        messages.error(request, error_message)


def _rate_limited(request, error: RateLimitExceeded):
    response = render(request, "web/429.html", status=429)
    response.headers["Retry-After"] = str(error.retry_after)
    return response


def _client_bound_identity(request, identity: str) -> str:
    return "\x00".join((client_ip(request) or "unknown-client", identity))


@method_decorator(never_cache, name="dispatch")
class MemberLoginView(LoginView):
    authentication_form = MemberAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        identity = _client_bound_identity(
            request,
            username_identity(request.POST.get("username", "")),
        )
        try:
            enforce_rate_limit(request, scope="login", identity=identity)
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        return super().post(request, *args, **kwargs)


class MemberLogoutView(LogoutView):
    next_page = "/"


@sensitive_variables("recovery_code")
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
                identity=_client_bound_identity(
                    request,
                    username_identity(request.POST.get("username", "")),
                ),
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
                user, recovery_code = register_member_with_recovery(
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
                _stage_recovery_code(
                    request,
                    recovery_code=recovery_code,
                    next_url=_safe_next(request, next_value, fallback=reverse("web:home")),
                )
                messages.success(request, "欢迎加入。请先保存你的账号恢复码。")
                return redirect("web:recovery-code")
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


@sensitive_variables("recovery_code")
@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def recovery_code_notice(request):
    reference = request.session.get(RECOVERY_NOTICE_SESSION_KEY)
    recovery_code = (
        RECOVERY_NOTICE_CACHE.get(_recovery_cache_key(reference))
        if isinstance(reference, str)
        else None
    )
    issued_at = request.session.get(RECOVERY_ISSUED_SESSION_KEY, 0)
    now = int(timezone.now().timestamp())
    expired = not isinstance(issued_at, int) or now - issued_at > RECOVERY_NOTICE_TTL_SECONDS
    if not recovery_code or expired:
        if isinstance(reference, str):
            RECOVERY_NOTICE_CACHE.delete(_recovery_cache_key(reference))
        request.session.pop(RECOVERY_NOTICE_SESSION_KEY, None)
        request.session.pop(RECOVERY_ISSUED_SESSION_KEY, None)
        request.session.pop(RECOVERY_NEXT_SESSION_KEY, None)
        if expired and recovery_code:
            messages.info(request, "恢复码显示时间已结束；如未保存，请在账号安全中重新生成。")
        return redirect("web:member-dashboard")
    next_value = request.session.get(RECOVERY_NEXT_SESSION_KEY, reverse("web:home"))
    if request.method == "POST":
        RECOVERY_NOTICE_CACHE.delete(_recovery_cache_key(reference))
        request.session.pop(RECOVERY_NOTICE_SESSION_KEY, None)
        request.session.pop(RECOVERY_ISSUED_SESSION_KEY, None)
        request.session.pop(RECOVERY_NEXT_SESSION_KEY, None)
        messages.success(request, "恢复码已确认保存。")
        return redirect(_safe_next(request, next_value, fallback=reverse("web:home")))
    return render(
        request,
        "registration/recovery_code.html",
        {"recovery_code": recovery_code},
    )


@sensitive_variables("recovery_code")
@sensitive_post_parameters("current_password")
@login_required
@never_cache
@require_POST
def recovery_code_rotate(request):
    try:
        enforce_rate_limit(
            request,
            scope="account_security",
            identity=str(request.user.public_id),
        )
    except RateLimitExceeded as error:
        return _rate_limited(request, error)
    form = RecoveryCodeRotateForm(request.POST)
    if not form.is_valid() or not request.user.check_password(
        form.cleaned_data.get("current_password", "")
    ):
        messages.error(request, "当前密码不正确，恢复码没有改变。")
        return redirect("web:member-password")
    recovery_code = issue_recovery_code(user=request.user)
    _stage_recovery_code(
        request,
        recovery_code=recovery_code,
        next_url=reverse("web:member-password"),
    )
    return redirect("web:recovery-code")


@sensitive_variables("replacement_code")
@sensitive_post_parameters("recovery_code", "password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def account_recovery(request):
    form = AccountRecoveryForm(request.POST or None)
    if request.method == "POST":
        identity = _client_bound_identity(
            request,
            "\x00".join(
                (
                    username_identity(request.POST.get("username", "")),
                    request.POST.get("email", "").strip().lower(),
                )
            ),
        )
        try:
            enforce_rate_limit(request, scope="recover", identity=identity)
        except RateLimitExceeded as error:
            return _rate_limited(request, error)
        if form.is_valid():
            try:
                user, replacement_code = recover_account(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    recovery_code=form.cleaned_data["recovery_code"],
                    new_password=form.cleaned_data["password2"],
                )
            except ValidationError:
                form.add_error(None, "账号信息或恢复码无效。")
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                _stage_recovery_code(
                    request,
                    recovery_code=replacement_code,
                    next_url=reverse("web:member-dashboard"),
                )
                messages.success(request, "密码已经更新，请保存新的恢复码。")
                return redirect("web:recovery-code")
    return render(request, "registration/account_recovery.html", {"form": form})


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
            | Q(external_reference__title__icontains=query)
            | Q(external_reference__excerpt__icontains=query)
            | Q(external_reference__author_name__icontains=query)
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


@require_http_methods(["GET", "HEAD"])
def community_rules(request):
    return render(request, "web/community_rules.html")


@require_http_methods(["GET", "HEAD"])
def privacy_notice(request):
    return render(request, "web/privacy_notice.html")


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
        form = EntryForm(request.POST, request.FILES, configuration=configuration)
        if form.is_valid():
            token = form.cleaned_data["nonce"]
            if not nonce_is_issued(request, purpose=purpose, token=token):
                form.add_error(None, "这个发布表单已经处理过，请刷新页面后再试。")
            else:
                validation_field = None
                try:
                    image_uploads = form.cleaned_data.get("images", [])
                    uploaded_video = form.cleaned_data.get("video")
                    if uploaded_video is not None:
                        try:
                            enforce_rate_limit(
                                request,
                                scope="video_process",
                                identity=str(request.user.public_id),
                            )
                        except RateLimitExceeded as error:
                            return _rate_limited(request, error)
                    expected_media_bytes = sum(upload.size for upload in image_uploads)
                    if uploaded_video is not None:
                        expected_media_bytes += uploaded_video.size + MAX_VIDEO_POSTER_BYTES
                    preflight_publish_capacity(
                        author=request.user,
                        moderation_mode=configuration.moderation_mode,
                        expected_media_bytes=expected_media_bytes,
                    )
                    validation_field = "images"
                    images = process_image_uploads(
                        uploads=image_uploads,
                        alt_texts=form.cleaned_data.get("image_alt_texts", []),
                        max_bytes=form.maximum_image_bytes,
                    )
                    validation_field = "video"
                    video = (
                        process_video_upload(upload=uploaded_video)
                        if uploaded_video is not None
                        else None
                    )
                    external_source = getattr(form, "external_source", None)
                    body = form.cleaned_data["body"]
                    if not body and external_source is not None:
                        body = (
                            "分享了一条 X 动态"
                            if external_source.provider == "x"
                            else "分享了一个 YouTube 视频"
                        )
                    validation_field = None
                    entry = publish_entry_once(
                        author=request.user,
                        body=body,
                        topics=form.cleaned_data["topics"],
                        purpose=purpose,
                        token=token,
                        images=images,
                        video=video,
                        source_url=form.cleaned_data.get("source_url", ""),
                    )
                except DuplicateSubmission:
                    form.add_error(None, "这个发布表单已经处理过，请刷新页面后再试。")
                except ValidationError as error:
                    form.add_error(validation_field, error)
                else:
                    if form.cleaned_data.get("source_url"):
                        try:
                            refresh_external_reference(
                                ExternalReference.objects.get(entry=entry),
                            )
                        except Exception:
                            logger.exception(
                                "external reference refresh failed for entry %s",
                                entry.public_id,
                            )
                    consume_nonce(request, purpose=purpose, token=token)
                    if entry.state == ContentState.PENDING:
                        messages.success(request, "内容已提交审核，通过后会出现在公开信息流。")
                        return redirect("web:home")
                    messages.success(request, "内容已经发布。")
                    return redirect("web:entry-detail", public_id=entry.public_id)
    else:
        form = EntryForm(
            configuration=configuration,
            initial={
                "nonce": issue_nonce(request, purpose=purpose),
                "source_url": request.GET.get("url", "")[:2_048],
            },
        )
    return render(request, "web/entry_form.html", {"form": form})


@never_cache
@require_http_methods(["GET", "HEAD"])
def attachment_file(request, public_id):
    attachment = get_object_or_404(
        Attachment.objects.select_related("entry", "entry__author"),
        public_id=public_id,
        mime_type="image/webp",
        file__endswith=".webp",
        width__isnull=False,
        height__isnull=False,
    )
    entry = attachment.entry
    expected_name = f"entries/{entry.public_id}/{attachment.public_id}.webp"
    if attachment.file.name != expected_name:
        raise Http404
    is_public = entry.state == ContentState.PUBLISHED and entry.author.is_active
    if not is_public:
        user = request.user
        is_own_pending = (
            user.is_authenticated
            and user.pk == entry.author_id
            and user.is_active
            and entry.state == ContentState.PENDING
        )
        can_review = (
            user.is_authenticated
            and user.is_active
            and user.is_staff
            and user.has_perm("publishing.change_entry")
        )
        if not (is_own_pending or can_review):
            raise Http404

    try:
        attachment.file.open("rb")
        if os.fstat(attachment.file.file.fileno()).st_size != attachment.byte_size:
            attachment.file.close()
            raise Http404
    except (FileNotFoundError, OSError, ValueError) as error:
        raise Http404 from error
    response = FileResponse(
        attachment.file,
        content_type="image/webp",
        as_attachment=False,
        filename=f"{attachment.public_id}.webp",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Length"] = str(attachment.byte_size)
    return response


def _entry_media_is_visible(request, entry) -> bool:
    if entry.state == ContentState.PUBLISHED and entry.author.is_active:
        return True
    user = request.user
    is_own_pending = (
        user.is_authenticated
        and user.pk == entry.author_id
        and user.is_active
        and entry.state == ContentState.PENDING
    )
    can_review = (
        user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.has_perm("publishing.change_entry")
    )
    return is_own_pending or can_review


def _media_response_headers(response, *, byte_size: int) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Length"] = str(byte_size)


def _stream_file_range(file_object, *, remaining: int):
    try:
        while remaining > 0:
            chunk = file_object.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_object.close()


def _range_not_satisfiable(video: VideoAsset):
    response = HttpResponse(status=416, content_type=video.mime_type)
    _media_response_headers(response, byte_size=0)
    response.headers["Content-Range"] = f"bytes */{video.byte_size}"
    return response


def _video_range_response(request, video: VideoAsset):
    try:
        video.file.open("rb")
        file_object = video.file.file
        if os.fstat(file_object.fileno()).st_size != video.byte_size:
            video.file.close()
            raise Http404
    except (FileNotFoundError, OSError, ValueError) as error:
        raise Http404 from error

    range_header = request.headers.get("Range", "")
    if not range_header:
        if request.method == "HEAD":
            video.file.close()
            response = HttpResponse(content_type=video.mime_type)
        else:
            response = FileResponse(file_object, content_type=video.mime_type)
        _media_response_headers(response, byte_size=video.byte_size)
        return response

    match = BYTE_RANGE_PATTERN.fullmatch(range_header.strip())
    if match is None or (match.group("start") == "" and match.group("end") == ""):
        video.file.close()
        return _range_not_satisfiable(video)

    start_value, end_value = match.group("start"), match.group("end")
    if start_value:
        start = int(start_value)
        end = min(int(end_value), video.byte_size - 1) if end_value else video.byte_size - 1
    else:
        suffix_length = int(end_value)
        if suffix_length <= 0:
            video.file.close()
            return _range_not_satisfiable(video)
        start = max(video.byte_size - suffix_length, 0)
        end = video.byte_size - 1
    if start >= video.byte_size or start > end:
        video.file.close()
        return _range_not_satisfiable(video)

    length = end - start + 1
    file_object.seek(start)
    if request.method == "HEAD":
        video.file.close()
        response = HttpResponse(status=206, content_type=video.mime_type)
    else:
        response = StreamingHttpResponse(
            _stream_file_range(file_object, remaining=length),
            status=206,
            content_type=video.mime_type,
        )
    _media_response_headers(response, byte_size=length)
    response.headers["Content-Range"] = f"bytes {start}-{end}/{video.byte_size}"
    return response


@never_cache
@require_http_methods(["GET", "HEAD"])
def video_file(request, public_id):
    video = get_object_or_404(
        VideoAsset.objects.select_related("entry", "entry__author"),
        public_id=public_id,
    )
    entry = video.entry
    extension = ".mp4" if video.mime_type == VideoMimeType.MP4 else ".webm"
    expected_name = f"entries/{entry.public_id}/{video.public_id}{extension}"
    if video.file.name != expected_name or not _entry_media_is_visible(request, entry):
        raise Http404
    return _video_range_response(request, video)


@never_cache
@require_http_methods(["GET", "HEAD"])
def video_poster_file(request, public_id):
    video = get_object_or_404(
        VideoAsset.objects.select_related("entry", "entry__author"),
        public_id=public_id,
    )
    entry = video.entry
    expected_name = f"entries/{entry.public_id}/{video.public_id}-poster.webp"
    if video.poster.name != expected_name or not _entry_media_is_visible(request, entry):
        raise Http404
    try:
        video.poster.open("rb")
        byte_size = os.fstat(video.poster.file.fileno()).st_size
        if byte_size != video.poster_byte_size:
            video.poster.close()
            raise Http404
    except (FileNotFoundError, OSError, ValueError) as error:
        raise Http404 from error
    response = FileResponse(
        video.poster.file,
        content_type="image/webp",
        as_attachment=False,
        filename=f"{video.public_id}-poster.webp",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Length"] = str(byte_size)
    return response


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
