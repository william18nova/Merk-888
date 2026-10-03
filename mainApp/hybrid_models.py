"""Dispositivos y recibos idempotentes del piloto híbrido."""
from uuid import uuid4
from django.db import models
from django.db.models import Q


class EquipoHibrido(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    nombre = models.CharField(max_length=80)
    punto = models.ForeignKey("PuntosPago", on_delete=models.PROTECT)
    activo = models.BooleanField(default=True)
    token_hash = models.CharField(max_length=64, blank=True)
    enlace_hash = models.CharField(max_length=64)
    enlace_vence = models.DateTimeField()
    creado_por = models.ForeignKey("Usuario", on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)
    visto_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "equipos_hibridos"
        constraints = [models.UniqueConstraint(fields=["punto"], condition=Q(activo=True), name="hibrido_equipo_activo_por_caja")]


class SesionHibrida(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    equipo = models.ForeignKey(EquipoHibrido, on_delete=models.PROTECT)
    usuario = models.ForeignKey("Usuario", on_delete=models.PROTECT)
    turno = models.ForeignKey("TurnoCaja", on_delete=models.PROTECT, null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    vence_en = models.DateTimeField()
    liberada_en = models.DateTimeField(null=True, blank=True)
    secuencia = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "sesiones_hibridas"
        constraints = [models.UniqueConstraint(fields=["equipo"], condition=Q(liberada_en__isnull=True), name="hibrido_sesion_por_equipo")]


class OperacionHibrida(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    sesion = models.ForeignKey(SesionHibrida, on_delete=models.PROTECT)
    secuencia = models.PositiveBigIntegerField()
    huella = models.CharField(max_length=64)
    venta = models.OneToOneField("Venta", on_delete=models.PROTECT, null=True, blank=True)
    tipo = models.CharField(max_length=30, default="sale")
    recibida_en = models.DateTimeField(auto_now_add=True)
    ocurrida_en = models.DateTimeField()
    respuesta = models.JSONField()

    class Meta:
        db_table = "operaciones_hibridas"
        constraints = [models.UniqueConstraint(fields=["sesion", "secuencia"], name="hibrido_secuencia_unica")]


class RecuperacionHibrida(models.Model):
    """Autorización y resultado auditables; nunca almacena secretos en claro."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    equipo = models.ForeignKey(EquipoHibrido, on_delete=models.PROTECT)
    creada_por = models.ForeignKey("Usuario", on_delete=models.PROTECT, related_name="recuperaciones_hibridas_creadas")
    aprobada_por = models.ForeignKey("Usuario", on_delete=models.PROTECT, related_name="recuperaciones_hibridas_aprobadas", null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    aprobada_en = models.DateTimeField(null=True, blank=True)
    completada_en = models.DateTimeField(null=True, blank=True)
    vence_en = models.DateTimeField()
    estado = models.CharField(max_length=16, default="issued")
    motivo = models.CharField(max_length=250)
    codigo_hash = models.CharField(max_length=64)
    nuevo_token_hash = models.CharField(max_length=64, blank=True)
    manifiesto_hash = models.CharField(max_length=64, blank=True)
    resumen = models.JSONField(default=dict)
    resultado = models.JSONField(default=dict)

    class Meta:
        db_table = "recuperaciones_hibridas"
        constraints = [models.UniqueConstraint(fields=["equipo"], condition=Q(estado__in=["issued", "review", "approved"]), name="hibrido_recuperacion_pendiente")]


class ReplicaHibrida(models.Model):
    """Corte autorizado e inmutable para descargas reanudables por equipo."""
    id = models.UUIDField(primary_key=True, editable=False)
    equipo = models.ForeignKey(EquipoHibrido, on_delete=models.CASCADE)
    sesion = models.ForeignKey(SesionHibrida, on_delete=models.CASCADE)
    base_id = models.UUIDField(null=True)
    solicitud_hash = models.CharField(max_length=64)
    alcance = models.JSONField()
    filas = models.JSONField()
    cambios = models.JSONField()
    resumen = models.JSONField()
    huella = models.CharField(max_length=64)
    cursor_ventas = models.JSONField(default=dict)
    creada_en = models.DateTimeField(auto_now_add=True)
    vence_en = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "replicas_hibridas"
