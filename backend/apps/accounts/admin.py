"""Admin registration for the custom User and teacher claims."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .forms import UserChangeForm, UserCreationForm
from .models import TeacherClaim, TeacherClaimStatus, User
from .services import TeacherLinkError, link_user_to_profile, reject_teacher_claim


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("个人信息"), {"fields": ("first_name", "last_name")}),
        (
            _("权限"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("重要日期"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    list_display = ("email", "first_name", "last_name", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)


@admin.register(TeacherClaim)
class TeacherClaimAdmin(admin.ModelAdmin):
    list_display = (
        "claimed_teacher_no",
        "claimed_formal_name",
        "user",
        "status",
        "matched_profile",
        "decided_by",
        "decided_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("user__email", "claimed_formal_name", "claimed_teacher_no")
    list_select_related = ("user", "matched_profile", "decided_by")
    readonly_fields = ("created_at", "updated_at", "decided_by", "decided_at")
    actions = ("approve_and_link", "reject")

    @admin.action(description="批准并关联到所选教师档案")
    def approve_and_link(self, request, queryset):
        for claim in queryset.select_related("user", "matched_profile"):
            if claim.status != TeacherClaimStatus.PENDING:
                self.message_user(
                    request,
                    f"{claim}：不是待处理状态，已跳过。",
                    level=messages.ERROR,
                )
                continue
            if not claim.matched_profile_id:
                self.message_user(
                    request,
                    f"{claim}：未填写匹配教师档案，需先编辑该申报并选择档案。",
                    level=messages.ERROR,
                )
                continue
            try:
                link_user_to_profile(
                    claim,
                    claim.matched_profile,
                    decided_by=request.user,
                    note="教务批准并关联",
                )
            except TeacherLinkError as exc:
                self.message_user(
                    request,
                    f"{claim}：{exc}",
                    level=messages.ERROR,
                )
                continue
            self.message_user(
                request,
                f"{claim}：已批准并关联到教师档案 {claim.matched_profile}。",
                level=messages.SUCCESS,
            )

    @admin.action(description="驳回")
    def reject(self, request, queryset):
        for claim in queryset.select_related("user"):
            if claim.status != TeacherClaimStatus.PENDING:
                self.message_user(
                    request,
                    f"{claim}：不是待处理状态，已跳过。",
                    level=messages.ERROR,
                )
                continue
            try:
                reject_teacher_claim(claim, decided_by=request.user)
            except TeacherLinkError as exc:
                self.message_user(
                    request,
                    f"{claim}：{exc}",
                    level=messages.ERROR,
                )
                continue
            self.message_user(request, f"{claim}：已驳回。", level=messages.SUCCESS)
