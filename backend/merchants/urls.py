from django.urls import path
from . import views

urlpatterns = [
    path('', views.MerchantListView.as_view(), name='merchant-list'),
    path('<int:merchant_id>/balance/', views.MerchantBalanceView.as_view(), name='merchant-balance'),
    path('invariant-check/', views.BalanceInvariantCheckView.as_view(), name='invariant-check'),
]