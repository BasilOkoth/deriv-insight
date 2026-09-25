from django.urls import path
from . import views

urlpatterns = [
    path("", views.overview, name="overview"),
    path("digits/", views.digit_lab, name="digit_lab"),
    path("conditional/", views.conditional_lab, name="conditional_lab"),
    path("cross-market/", views.cross_market_lab, name="cross_market_lab"),
    path("balanced/", views.balanced_lab, name="balanced_lab"),
    path(
        "balanced/batch/<str:batch_id>/step/",
        views.balanced_batch_step,
        name="balanced_batch_step",
    ),
    path("radar/", views.edge_radar, name="edge_radar"),
    path("backtests/", views.backtests, name="backtests"),
    path("demo/", views.demo_lab, name="demo_lab"),
    path("settings/", views.settings_view, name="settings"),
    path("health/", views.health, name="health"),
]
