"""天梯路由(挂载在 /ladder/ 前缀下)。"""
from django.urls import path

from . import views

urlpatterns = [
    path("<int:sku>/<str:ladder_type>", views.seasons, name="ladder-seasons"),
    path(
        "<int:sku>/<str:ladder_type>/<str:season>/listsearch",
        views.list_search,
        name="ladder-listsearch",
    ),
    path(
        "<int:sku>/<str:ladder_type>/<str:season>/rungsearch",
        views.rung_search,
        name="ladder-rungsearch",
    ),
]
