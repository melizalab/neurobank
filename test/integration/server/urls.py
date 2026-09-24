from django.urls import include, path

urlpatterns = [
    path("neurobank/", include("nbank_registry.urls")),
]
