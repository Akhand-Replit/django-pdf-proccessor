from django.urls import path
from . import views

urlpatterns = [
    # This URL pattern points to our upload_view function.
    # The name='upload_view' is important as it's referenced in our HTML form.
    path('', views.upload_view, name='upload_view'),
]
