from django.urls import path
from . import views

urlpatterns = [
    path('', views.PayoutCreateView.as_view(), name='payout-create'),
    path('list/', views.PayoutListView.as_view(), name='payout-list'),
    path('<uuid:payout_id>/', views.PayoutDetailView.as_view(), name='payout-detail'),
]