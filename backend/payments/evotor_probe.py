"""Isolated exchange test. No API here can settle a payment or print a receipt."""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.crypto import salted_hmac
from core.models import Organization
from core.tenancy import current_organization
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsAdminRole
from .models import EvotorProbeCommand, EvotorProbeDevice, EvotorProbePairing

ACTIVE = ("queued", "received")
FINAL = ("echo_ok", "receipt_opened", "error", "needs_review")


def page(request):
    return render(request, "payments/evotor_probe.html")


def command_data(c):
    return {
        "id": str(c.pk), "kind": c.kind, "status": c.status,
        "test_only": True, "result": c.result,
        "remaining_seconds": max(0, int((c.expires_at - timezone.now()).total_seconds())),
        "created_at": c.created_at, "expires_at": c.expires_at,
        "received_at": c.received_at, "finished_at": c.finished_at,
        "receipt": {"name": "ТЕСТ ОБМЕНА — НЕ ОПЛАЧИВАТЬ", "price": "1.00", "quantity": "1"},
    }


def expire(device):
    # A received command might already have touched the SDK: never retry it as new.
    EvotorProbeCommand.objects.filter(device=device, status="queued", expires_at__lt=timezone.now()).update(status="expired")


class DeviceInput(serializers.Serializer):
    name = serializers.CharField(max_length=80, default="Тестовый Эвотор")


class CommandInput(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=["echo", "open_receipt"])


class ResultInput(serializers.Serializer):
    status = serializers.ChoiceField(choices=FINAL)
    message = serializers.CharField(max_length=500, allow_blank=True, default="")
    receipt_uuid = serializers.CharField(max_length=100, allow_blank=True, default="")


class Devices(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(list(EvotorProbeDevice.objects.order_by("-created_at").values("id", "name", "active", "last_seen")))

    def post(self, request):
        data = DeviceInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=current_organization().pk)
            EvotorProbePairing.objects.filter(expires_at__gt=timezone.now()).update(expires_at=timezone.now())
            code = f"{secrets.randbelow(1000000):06d}"
            pairing = EvotorProbePairing.objects.create(
                name=data.validated_data["name"], code_hash=code_digest(org.pk, code),
                expires_at=timezone.now() + timedelta(minutes=5),
            )
        return Response({"pairing_id": pairing.pk, "code": code, "expires_at": pairing.expires_at}, status=201)


def code_digest(org_id, code):
    return salted_hmac("evotor-probe-pairing", f"{org_id}:{code}", algorithm="sha256").hexdigest()


class PairingInput(serializers.Serializer):
    code = serializers.RegexField(r"^[0-9]{6}$", max_length=6)
    # Random installation secret, persisted before sending. Allows response-loss retries.
    client_nonce = serializers.RegexField(r"^[a-f0-9]{64}$", max_length=64)


class PairingClaim(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        data = PairingInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=current_organization().pk)
            p = EvotorProbePairing.objects.filter(expires_at__gt=timezone.now()).order_by("-created_at").first()
            if p is None or p.attempts >= 5:
                return Response({"detail": "Код истёк или заблокирован. Получите новый код в Падаче."}, status=400)
            nonce = data.validated_data["client_nonce"]
            digest = code_digest(org.pk, data.validated_data["code"])
            if not secrets.compare_digest(p.code_hash, digest) or (p.claimed_by and not secrets.compare_digest(p.claimed_by, nonce)):
                p.attempts += 1
                p.save(update_fields=["attempts"])
                return Response({"detail": "Код неверный или уже использован."}, status=400)
            token = salted_hmac("evotor-probe-device", f"{p.pk}:{nonce}", algorithm="sha256").hexdigest()
            if p.device_id is None:
                p.device = EvotorProbeDevice.objects.create(name=p.name, token_hash=hashlib.sha256(token.encode()).hexdigest())
                p.claimed_by = nonce
                p.save(update_fields=["device", "claimed_by"])
            elif not p.device.active:
                return Response({"detail": "Касса отключена. Получите новый код."}, status=400)
            return Response({"id": p.device_id, "token": token, "name": p.device.name})


class PairingStatus(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request, pairing_id):
        p = get_object_or_404(EvotorProbePairing.objects, pk=pairing_id)
        state = "connected" if p.device_id else "waiting"
        if state == "waiting" and (p.expires_at <= timezone.now() or p.attempts >= 5):
            state = "expired"
        return Response({"status": state, "device_id": p.device_id})



class DeviceDetail(APIView):
    permission_classes = [IsAdminRole]

    def delete(self, request, device_id):
        d = get_object_or_404(EvotorProbeDevice.objects, pk=device_id)
        d.active = False
        d.save(update_fields=["active"])
        return Response(status=204)


class Commands(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request, device_id):
        d = get_object_or_404(EvotorProbeDevice.objects, pk=device_id)
        expire(d)
        return Response([command_data(c) for c in EvotorProbeCommand.objects.filter(device=d).order_by("-created_at")[:20]])

    def post(self, request, device_id):
        data = CommandInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            d = get_object_or_404(EvotorProbeDevice.objects.select_for_update(), pk=device_id, active=True)
            old = EvotorProbeCommand.objects.filter(pk=data.validated_data["id"]).first()
            if old:
                if old.device_id != d.pk or old.kind != data.validated_data["kind"]:
                    return Response({"detail": "ID уже использован для другой команды"}, status=409)
                return Response(command_data(old))
            expire(d)
            if EvotorProbeCommand.objects.filter(device=d, status__in=ACTIVE).exists():
                return Response({"detail": "Предыдущая команда ещё не завершена"}, status=409)
            c = EvotorProbeCommand.objects.create(device=d, expires_at=timezone.now() + timedelta(seconds=60), **data.validated_data)
        return Response(command_data(c), status=201)


class ProbeAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "EvotorProbe"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "").split()
        if len(header) != 2 or header[0] != "EvotorProbe":
            raise AuthenticationFailed("Требуется ключ тестового устройства")
        token_hash = hashlib.sha256(header[1].encode()).hexdigest()
        device = EvotorProbeDevice.objects.filter(token_hash=token_hash, active=True).first()
        if device is None:
            raise AuthenticationFailed("Ключ устройства недействителен для этого заведения")
        return device, device


class IsProbeDevice(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, EvotorProbeDevice)


class DeviceAPI(APIView):
    authentication_classes = [ProbeAuthentication]
    permission_classes = [IsProbeDevice]


class NextCommand(DeviceAPI):
    def post(self, request):
        with transaction.atomic():
            d = EvotorProbeDevice.objects.select_for_update().get(pk=request.auth.pk)
            d.last_seen = timezone.now()
            d.save(update_fields=["last_seen"])
            expire(d)
            c = EvotorProbeCommand.objects.filter(device=d, status__in=ACTIVE).order_by("created_at").first()
            if c is None:
                return Response(status=204)
            if c.status == "queued":
                c.status = "received"
                c.received_at = timezone.now()
                c.save(update_fields=["status", "received_at"])
            return Response(command_data(c))


class CommandResult(DeviceAPI):
    def post(self, request, command_id):
        data = ResultInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            c = get_object_or_404(EvotorProbeCommand.objects.select_for_update(), pk=command_id, device=request.auth)
            result = data.validated_data
            if c.status in FINAL:
                if c.status != result["status"] or c.result != result:
                    return Response({"detail": "Результат уже зафиксирован"}, status=409)
                return Response(command_data(c))
            if c.status != "received":
                return Response({"detail": "Команда не получена устройством"}, status=409)
            if (result["status"] == "echo_ok" and c.kind != "echo") or (result["status"] == "receipt_opened" and c.kind != "open_receipt"):
                raise serializers.ValidationError("Результат не соответствует команде")
            c.status, c.result, c.finished_at = result["status"], result, timezone.now()
            c.save(update_fields=["status", "result", "finished_at"])
        return Response(command_data(c))
