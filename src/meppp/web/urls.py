from django.urls import path

from meppp.moderation.models import SubjectType

from . import member_views, views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.MemberLoginView.as_view(), name="login"),
    path("logout/", views.MemberLogoutView.as_view(), name="logout"),
    path("join/", views.register, name="register"),
    path("me/", member_views.dashboard, name="member-dashboard"),
    path("me/settings/", member_views.settings, name="member-settings"),
    path(
        "me/password/",
        member_views.MemberPasswordChangeView.as_view(),
        name="member-password",
    ),
    path(
        "me/entry/<uuid:public_id>/withdraw/",
        member_views.entry_withdraw,
        name="member-entry-withdraw",
    ),
    path(
        "me/comment/<uuid:public_id>/withdraw/",
        member_views.comment_withdraw,
        name="member-comment-withdraw",
    ),
    path("write/", views.entry_create, name="entry-create"),
    path("entry/<uuid:public_id>/", views.entry_detail, name="entry-detail"),
    path("entry/<uuid:public_id>/comment/", views.comment_create, name="comment-create"),
    path("entry/<uuid:public_id>/like/", views.entry_like, name="entry-like"),
    path("member/<uuid:public_id>/", views.member_profile, name="member-profile"),
    path("member/<uuid:public_id>/follow/", views.member_follow, name="member-follow"),
    path(
        "report/member/<uuid:public_id>/",
        views.report_create,
        {"subject_type": SubjectType.USER},
        name="report-member",
    ),
    path(
        "report/entry/<uuid:public_id>/",
        views.report_create,
        {"subject_type": SubjectType.ENTRY},
        name="report-entry",
    ),
    path(
        "report/comment/<uuid:public_id>/",
        views.report_create,
        {"subject_type": SubjectType.COMMENT},
        name="report-comment",
    ),
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/read/", views.notifications_read, name="notifications-read"),
]
