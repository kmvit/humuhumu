from django.urls import path
from . import evotor_probe as v

urlpatterns = [
    path("pair/", v.PairingClaim.as_view()),
    path("pairing/<uuid:pairing_id>/", v.PairingStatus.as_view()),
    path("devices/", v.Devices.as_view()),
    path("devices/<uuid:device_id>/", v.DeviceDetail.as_view()),
    path("devices/<uuid:device_id>/commands/", v.Commands.as_view()),
    path("next/", v.NextCommand.as_view()),
    path("commands/<uuid:command_id>/result/", v.CommandResult.as_view()),
]
