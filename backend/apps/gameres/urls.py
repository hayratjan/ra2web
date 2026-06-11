"""战绩上报路由(挂载在 /wgameres/ 前缀下)。"""
from django.urls import path

from . import views

urlpatterns = [
    path("<int:sku>", views.submit, name="wgameres-submit"),
]
