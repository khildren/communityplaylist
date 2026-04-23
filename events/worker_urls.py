from django.urls import path
from . import worker_api

urlpatterns = [
    path("tasks/",                    worker_api.pending_tasks,   name="worker_tasks"),
    path("tasks/<int:task_id>/claim/",    worker_api.claim_task,  name="worker_claim"),
    path("tasks/<int:task_id>/complete/", worker_api.complete_task, name="worker_complete"),
    path("tasks/<int:task_id>/error/",    worker_api.error_task,  name="worker_error"),
    path("trigger/<str:cmd_name>/",   worker_api.trigger_command, name="worker_trigger"),
]
