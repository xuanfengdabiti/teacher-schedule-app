"""Serializers for registration, login and the mobile ``me`` contract."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import ContactVisibility, TeacherProfile, Term
from apps.accounts.models import TeacherClaim, TeacherClaimStatus, normalize_user_email
from apps.accounts.services import (
    revoke_all_refresh_tokens,
    teacher_claim_for,
    teacher_profile_for,
    teacher_status_for,
)

try:
    from allauth.account import app_settings as allauth_settings
    from allauth.account.forms import default_token_generator
    from allauth.account.internal.flows.password_reset import request_password_reset
    from allauth.account.models import EmailAddress
    from dj_rest_auth.forms import AllAuthPasswordResetForm
    from dj_rest_auth.registration.serializers import RegisterSerializer
    from dj_rest_auth.serializers import (
        LoginSerializer,
        PasswordChangeSerializer,
        PasswordResetConfirmSerializer,
        PasswordResetSerializer,
    )
except ImportError as exc:  # pragma: no cover - missing installation
    raise ImportError("dj-rest-auth / django-allauth must be installed for accounts.") from exc


class TeacherRegisterSerializer(RegisterSerializer):
    """Registration with teacher identity fields and no username."""

    username = None
    email = serializers.EmailField()
    password1 = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)
    formal_name = serializers.CharField(max_length=100)
    teacher_no = serializers.CharField(max_length=64)

    def validate_email(self, email: str) -> str:
        email = normalize_user_email(email)
        if get_user_model().objects.filter(email=email).exists():
            raise serializers.ValidationError("该邮箱已注册，请直接登录或找回密码。")
        return super().validate_email(email)

    def validate_formal_name(self, formal_name: str) -> str:
        formal_name = formal_name.strip()
        if not formal_name:
            raise serializers.ValidationError("请填写正式姓名。")
        return formal_name

    def validate_teacher_no(self, teacher_no: str) -> str:
        teacher_no = teacher_no.strip()
        if not teacher_no:
            raise serializers.ValidationError("请填写工号。")
        return teacher_no

    def get_cleaned_data(self) -> dict[str, str]:
        return {
            "email": self.validated_data.get("email", ""),
            "password1": self.validated_data.get("password1", ""),
        }

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        TeacherClaim.objects.create(
            user=user,
            claimed_formal_name=self.validated_data["formal_name"],
            claimed_teacher_no=self.validated_data["teacher_no"],
        )
        return user


class TeacherLoginSerializer(LoginSerializer):
    """Email-only login with a stable explanation for unverified addresses."""

    username = None
    email = serializers.EmailField()

    @staticmethod
    def validate_email_verification_status(user, email: str | None = None) -> None:
        if allauth_settings.EMAIL_VERIFICATION != allauth_settings.EmailVerificationMethod.MANDATORY:
            return
        if not EmailAddress.objects.filter(user=user, email=user.email, verified=True).exists():
            raise serializers.ValidationError("邮箱尚未验证，请先打开验证邮件完成验证。")


class TeacherPasswordResetForm(AllAuthPasswordResetForm):
    """Keep dj-rest-auth's active-user filtering but use allauth's current flow.

    dj-rest-auth 7.2.0's stock form still reads the deprecated
    ``AUTHENTICATION_METHOD`` property and reverses a web reset URL.  allauth's
    own password-reset flow uses the account adapter instead, so no JWT,
    password or token algorithm is reimplemented here.
    """

    def save(self, request, **kwargs):
        token_generator = kwargs.get("token_generator", default_token_generator)
        request_password_reset(
            request,
            self.cleaned_data["email"],
            self.users,
            token_generator,
        )
        return self.cleaned_data["email"]


class TeacherPasswordResetSerializer(PasswordResetSerializer):
    """Use the App deep link and avoid deprecation warnings."""

    @property
    def password_reset_form_class(self):
        return TeacherPasswordResetForm


class RevokeOnPasswordChangeSerializer(PasswordChangeSerializer):
    """Revoke every refresh token after a successful password change."""

    @transaction.atomic
    def save(self):
        result = super().save()
        revoke_all_refresh_tokens(self.user)
        return result


class RevokeOnPasswordResetConfirmSerializer(PasswordResetConfirmSerializer):
    """Revoke every refresh token after a successful password reset."""

    @transaction.atomic
    def save(self):
        result = super().save()
        revoke_all_refresh_tokens(self.user)
        return result


class CurrentTermSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()


class TeacherMeSerializer(serializers.Serializer):
    teacher_no = serializers.CharField()
    formal_name = serializers.CharField()
    display_name = serializers.CharField()
    department = serializers.CharField()
    phone = serializers.CharField()
    phone_visibility = serializers.ChoiceField(choices=ContactVisibility.choices)
    wechat = serializers.CharField()
    wechat_visibility = serializers.ChoiceField(choices=ContactVisibility.choices)


class TeacherClaimMeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TeacherClaimStatus.choices)
    claimed_formal_name = serializers.CharField()
    claimed_teacher_no = serializers.CharField()


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    email_verified = serializers.SerializerMethodField()
    teacher_status = serializers.SerializerMethodField()
    assignment_scope_version = serializers.SerializerMethodField()
    current_term = serializers.SerializerMethodField()
    teacher = serializers.SerializerMethodField()
    claim = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_email_verified(self, obj) -> bool:
        return EmailAddress.objects.filter(user=obj, primary=True, verified=True).exists()

    @extend_schema_field(serializers.ChoiceField(choices=("active", "pending", "disabled", "unlinked")))
    def get_teacher_status(self, obj) -> str:
        return teacher_status_for(obj)

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_assignment_scope_version(self, obj) -> int | None:
        profile = teacher_profile_for(obj)
        return profile.assignment_scope_version if profile else None

    @extend_schema_field(CurrentTermSerializer(allow_null=True))
    def get_current_term(self, obj):
        today = timezone.localdate()
        term = Term.objects.filter(start_date__lte=today, end_date__gte=today).order_by("start_date", "code").first()
        return CurrentTermSerializer(term).data if term else None

    @extend_schema_field(TeacherMeSerializer(allow_null=True))
    def get_teacher(self, obj):
        profile = teacher_profile_for(obj)
        return TeacherMeSerializer(profile).data if profile else None

    @extend_schema_field(TeacherClaimMeSerializer(allow_null=True))
    def get_claim(self, obj):
        claim = teacher_claim_for(obj)
        if not claim or claim.status not in {TeacherClaimStatus.PENDING, TeacherClaimStatus.REJECTED}:
            return None
        return TeacherClaimMeSerializer(claim).data


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="要加入 blacklist 的 refresh token。")


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class PasswordChangeResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    reauthentication_required = serializers.BooleanField()


class MeUpdateSerializer(serializers.ModelSerializer):
    """Only self-service profile fields are writable through PATCH /me/."""

    class Meta:
        model = TeacherProfile
        fields = (
            "display_name",
            "department",
            "phone",
            "phone_visibility",
            "wechat",
            "wechat_visibility",
        )

    def validate(self, attrs):
        unexpected = [field for field in self.initial_data if field not in self.fields]
        if unexpected:
            raise serializers.ValidationError({field: ["该字段不可通过此接口修改。"] for field in unexpected})
        return attrs
