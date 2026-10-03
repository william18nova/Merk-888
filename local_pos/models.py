"""Tablas solo de la instalación local; no añadir a la app mainApp de la nube."""
from django.conf import settings
from django.db import models


class LocalNode(models.Model):
    id = models.UUIDField(primary_key=True)
    sequence = models.PositiveBigIntegerField(default=0)


class LocalCommand(models.Model):
    operation_id = models.UUIDField(primary_key=True)
    node = models.ForeignKey(LocalNode, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=100)
    version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    local_result = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default="pending", choices=[("intent", "Preparada"), ("pending", "Pendiente"), ("accepted", "Aceptada"), ("conflict", "Revisión")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["node", "sequence"], name="local_node_sequence_unique")]


class ReplicaState(models.Model):
    node = models.OneToOneField(LocalNode, primary_key=True, on_delete=models.PROTECT)
    local_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    server = models.CharField(max_length=250)
    device_id = models.UUIDField()
    active_id = models.UUIDField(null=True)
    scope = models.JSONField(default=dict)
    manifest = models.CharField(max_length=64, blank=True)
    synchronized_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    blocked = models.BooleanField(default=False)
    status = models.CharField(max_length=160, default="Esperando descarga inicial")
    sale_cursor = models.JSONField(default=dict)


class ReplicaRow(models.Model):
    node = models.ForeignKey(LocalNode, on_delete=models.PROTECT)
    entity = models.CharField(max_length=30)
    source_id = models.CharField(max_length=100)
    values = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["node", "entity", "source_id"], name="local_replica_row_unique")]


class ReplicaTransfer(models.Model):
    id = models.UUIDField(primary_key=True)
    node = models.ForeignKey(LocalNode, on_delete=models.PROTECT)
    session_id = models.UUIDField()
    base_id = models.UUIDField(null=True)
    descriptor = models.JSONField(default=dict)
    next_offset = models.PositiveIntegerField(default=0)
    downloaded = models.BooleanField(default=False)
    applied = models.BooleanField(default=False)
    abandoned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class ReplicaPage(models.Model):
    transfer = models.ForeignKey(ReplicaTransfer, on_delete=models.CASCADE)
    offset = models.PositiveIntegerField()
    changes = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["transfer", "offset"], name="local_replica_page_unique")]


class LocalSaleSession(models.Model):
    node = models.OneToOneField(LocalNode, primary_key=True, on_delete=models.PROTECT)
    session_id = models.UUIDField()
    data = models.JSONField()
    products = models.JSONField(default=dict)
    sequence = models.PositiveBigIntegerField(default=0)
    last_clock = models.DateTimeField(null=True)
    online = models.BooleanField(default=False)
    error = models.CharField(max_length=200, blank=True)


class LocalOperator(models.Model):
    """Cuenta local separada por identidad remota, sin contraseñas de la nube."""
    node = models.ForeignKey(LocalNode, on_delete=models.PROTECT)
    cloud_user_id = models.PositiveBigIntegerField()
    cloud_username = models.CharField(max_length=100)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["node", "cloud_user_id"], name="local_operator_cloud_unique")]


class LocalHandoff(models.Model):
    """Registro inmutable del relevo; no cambia autores de operaciones pasadas."""
    session_id = models.UUIDField(primary_key=True)
    node = models.ForeignKey(LocalNode, on_delete=models.PROTECT)
    previous_session_id = models.UUIDField()
    previous_actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="local_handoffs_out")
    next_actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="local_handoffs_in")
    previous_cloud_user_id = models.PositiveBigIntegerField()
    next_cloud_user_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
