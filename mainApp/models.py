from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from decimal import Decimal
from datetime import date
from django.db import models, transaction
from django.db.models import Q
from django.forms import ValidationError
from django.utils import timezone
from django.db.models import F

class Sucursal(models.Model):
    sucursalid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100, unique=True)
    direccion = models.TextField(null=True, blank=True)
    telefono = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        db_table = 'sucursales'

    def __str__(self):
        return self.nombre

class Categoria(models.Model):
    categoriaid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'categorias'

    def __str__(self):
        return self.nombre

class Producto(models.Model):
    productoid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100, unique=True, db_index=True)
    descripcion = models.TextField(null=True, blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, null=True, blank=True)
    codigo_de_barras = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    iva = models.FloatField(default=0.0)

    # NUEVOS CAMPOS (columnas nuevas en la tabla productos)
    impuesto_consumo = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    icui = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    ibua = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    
    rentabilidad = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    precio_anterior = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = 'productos'
        indexes = [
            models.Index(fields=['nombre']),
            models.Index(fields=['productoid']),
            models.Index(fields=['codigo_de_barras']),
        ]

    def __str__(self):
        return self.nombre


class Inventario(models.Model):
    inventarioid = models.AutoField(primary_key=True)
    productoid = models.ForeignKey(Producto, on_delete=models.CASCADE, db_column='productoid')
    sucursalid = models.ForeignKey(Sucursal, on_delete=models.CASCADE, db_column='sucursalid')
    cantidad = models.IntegerField()

    class Meta:
        db_table = 'inventario'

    def __str__(self):
        return f"Inventario de {self.productoid.nombre} en {self.sucursalid.nombre}"

class Proveedor(models.Model):
    proveedorid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    empresa = models.CharField(max_length=100, null=True, blank=True)
    telefono = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(max_length=100, null=True, blank=True)
    direccion = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'proveedores'

    def __str__(self):
        return self.nombre

class PreciosProveedor(models.Model):
    id = models.AutoField(primary_key=True)
    productoid = models.ForeignKey(Producto, on_delete=models.CASCADE, db_column='productoid')
    proveedorid = models.ForeignKey(Proveedor, on_delete=models.CASCADE, db_column='proveedorid')
    precio = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'preciosproveedor'
        unique_together = ('productoid', 'proveedorid')

    def __str__(self):
        return f"Precio del producto {self.productoid.nombre} por el proveedor {self.proveedorid.nombre}"
    
class PuntosPago(models.Model):
    puntopagoid = models.AutoField(primary_key=True)
    sucursalid = models.ForeignKey('Sucursal', on_delete=models.CASCADE, db_column='sucursalid')
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=100, blank=True, null=True)
    dinerocaja = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    class Meta:
        db_table = 'puntospago'

    def __str__(self):
        return self.nombre

class Rol(models.Model):
    rolid = models.AutoField(primary_key=True)  # Asegúrate de definir rolid como clave primaria
    nombre = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'roles'

    def __str__(self):
        return self.nombre

class UsuarioManager(BaseUserManager):
    def create_user(self, nombreusuario, password=None, **extra_fields):
        if not nombreusuario:
            raise ValueError('El nombre de usuario es obligatorio')
        user = self.model(nombreusuario=nombreusuario, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, nombreusuario, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True or extra_fields.get('is_superuser') is not True:
            raise ValueError('El superusuario debe tener is_staff=True e is_superuser=True.')
        return self.create_user(nombreusuario, password, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    class Meta:
        db_table = 'usuarios'

    usuarioid     = models.AutoField(db_column='usuarioid', primary_key=True)
    nombreusuario = models.CharField(db_column='nombreusuario', max_length=100, unique=True)

    # Aquí dejamos el atributo en Python como `password`, pero le decimos
    # que en la BD el nombre de columna es `contraseña`
    password      = models.CharField(db_column='contraseña', max_length=255)

    rolid = models.ForeignKey('Rol', db_column='rolid', null=True, blank=True, on_delete=models.SET_NULL)

    last_login    = models.DateTimeField(db_column='last_login', blank=True, null=True)
    is_active     = models.BooleanField(db_column='is_active', default=True)
    is_staff      = models.BooleanField(db_column='is_staff', default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = 'nombreusuario'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.nombreusuario
    
class Empleado(models.Model):
    empleadoid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20, unique=True)
    email = models.CharField(max_length=100, unique=True)
    direccion = models.TextField(null=True, blank=True)
    puesto = models.CharField(max_length=50, null=True, blank=True)
    numerodocumento = models.CharField(max_length=50, unique=True)
    usuarioid = models.OneToOneField(Usuario, on_delete=models.CASCADE, db_column='usuarioid', null=True, blank=True)
    sucursalid = models.ForeignKey(Sucursal, on_delete=models.SET_NULL, db_column='sucursalid', null=True, blank=True)

    class Meta:
        db_table = 'empleados'

    def __str__(self):
        return f'{self.nombre} {self.apellido}'
    
class HorariosNegocio(models.Model):
    horarioid = models.AutoField(primary_key=True)
    dia_semana = models.CharField(max_length=10)
    horaapertura = models.TimeField()
    horacierre = models.TimeField()
    sucursalid = models.ForeignKey(Sucursal, on_delete=models.CASCADE, db_column='sucursalid')

    class Meta:
        db_table = 'horariosnegocio'

    def __str__(self):
        return f"{self.dia_semana} - {self.sucursalid.nombre}"
    
class HorarioCaja(models.Model):
    horariocajaid = models.AutoField(primary_key=True)  # Corrige el nombre del campo aquí
    puntopagoid = models.ForeignKey(PuntosPago, on_delete=models.CASCADE, related_name='horarios_caja')
    dia_semana = models.CharField(max_length=3)
    horaapertura = models.TimeField()
    horacierre = models.TimeField()

    class Meta:
        db_table = 'horarioscajas'

class Cliente(models.Model):
    clienteid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    email = models.CharField(max_length=100, blank=True, null=True)
    numerodocumento = models.CharField(max_length=50)

    class Meta:
        db_table = 'clientes'

    def __str__(self):
        return f'{self.nombre} {self.apellido}'


class Venta(models.Model):
    ventaid = models.AutoField(primary_key=True)
    fecha = models.DateField()
    hora = models.TimeField()

    clienteid = models.ForeignKey('Cliente', on_delete=models.CASCADE, null=True, blank=True, db_column='clienteid')
    empleadoid = models.ForeignKey('Empleado', on_delete=models.CASCADE, db_column='empleadoid')
    sucursalid = models.ForeignKey('Sucursal', on_delete=models.CASCADE, db_column='sucursalid')
    puntopagoid = models.ForeignKey('PuntosPago', on_delete=models.CASCADE, db_column='puntopagoid')

    total = models.DecimalField(max_digits=10, decimal_places=2)

    # ✅ si es pago único: "efectivo", "nequi"... si es mixto: "mixto"
    mediopago = models.CharField(max_length=50)

    class Meta:
        db_table = 'ventas'


class DetalleVenta(models.Model):
    detalleventaid = models.AutoField(primary_key=True)
    ventaid = models.ForeignKey(Venta, on_delete=models.CASCADE, db_column='ventaid')
    productoid = models.ForeignKey('Producto', on_delete=models.CASCADE, db_column='productoid')
    cantidad = models.IntegerField()
    preciounitario = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'detallesventas'


class PagoVenta(models.Model):
    id = models.BigAutoField(primary_key=True)

    ventaid = models.ForeignKey(
        Venta,
        on_delete=models.CASCADE,
        db_column="ventaid",
        related_name="pagos",
    )

    # 👇 OJO: en la BD la columna se llama "metodo"
    medio_pago = models.CharField(
        max_length=50,
        db_column="metodo",
    )

    monto = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "venta_pagos"

    def __str__(self):
        return f"Venta {self.ventaid_id} - {self.medio_pago} - {self.monto}"

class PedidoProveedor(models.Model):
    ESTADOS = [
        ('En espera', 'En espera'),
        ('Recibido',  'Recibido'),
        ('Devuelto',  'Devuelto'),
    ]

    pedidoid               = models.AutoField(primary_key=True)
    proveedorid            = models.ForeignKey('Proveedor',
                                               on_delete=models.CASCADE,
                                               db_column='proveedorid')
    sucursalid             = models.ForeignKey('Sucursal',
                                               on_delete=models.CASCADE,
                                               db_column='sucursalid')
    fechapedido            = models.DateField(auto_now_add=True)
    fechaestimadaentrega   = models.DateField(null=True, blank=True)
    costototal             = models.DecimalField(max_digits=10,
                                                decimal_places=2,
                                                default=Decimal('0.00'))
    estado                 = models.CharField(max_length=50,
                                              choices=ESTADOS,
                                              default='En espera')
    comentario             = models.TextField(null=True, blank=True)

    # Nuevos campos para "Recibido"
    fecha_recibido         = models.DateField(null=True, blank=True)
    monto_pagado           = models.DecimalField(max_digits=12,
                                                decimal_places=2,
                                                null=True, blank=True)
    caja_pago               = models.ForeignKey('PuntosPago',
                                               on_delete=models.SET_NULL,
                                               null=True, blank=True,
                                               db_column='caja_pagoid')

    class Meta:
        db_table = 'pedidosproveedor'
        constraints = [
            models.CheckConstraint(
                check=Q(estado__in=[e[0] for e in [
        ('En espera', 'En espera'),
        ('Recibido',  'Recibido'),
        ('Devuelto',  'Devuelto'),
    ]]),
                name='pedidosproveedor_estado_check',
            ),
        ]

    def __str__(self):
        return f"Pedido {self.pedidoid} - {self.proveedorid.nombre}"

class DetallePedidoProveedor(models.Model):
    detallepedidoid = models.AutoField(primary_key=True)
    pedidoid = models.ForeignKey(PedidoProveedor, on_delete=models.CASCADE, db_column='pedidoid')
    productoid = models.ForeignKey('Producto', on_delete=models.CASCADE, db_column='productoid')
    cantidad = models.IntegerField()
    preciounitario = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'detallespedidosproveedor'

    def __str__(self):
        return f"Detalle {self.detallepedidoid} - {self.productoid.nombre}"
    

            
class Permiso(models.Model):
    """
    Mapea la tabla existente public.persmisos
    Campos según dump: permisoid (PK), nombre, descripcion
    """
    permisoid = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "permisos"
        verbose_name = "permiso"
        verbose_name_plural = "permisos"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

class RolPermiso(models.Model):
    id      = models.BigAutoField(primary_key=True, db_column="id")
    rol     = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        db_column="rolid",
        related_name="rolespermisos",
    )
    permiso = models.ForeignKey(
        Permiso,
        on_delete=models.CASCADE,
        db_column="permisoid",
        related_name="rolespermisos",
    )

    class Meta:
        db_table = "rolespermisos"     # nombre EXACTO de tu tabla
        managed  = False               # la tabla ya existe (creada/alterada a mano)
        constraints = [
            models.UniqueConstraint(
                fields=["rol", "permiso"],
                name="ux_rolespermisos_rol_perm",  # coincide con tu índice único
            )
        ]

    def __str__(self):
        return f"{self.rol} ↔ {self.permiso}"


class UsuarioPermiso(models.Model):
    id = models.BigAutoField(primary_key=True, db_column="id")
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        db_column="usuarioid",
        related_name="permisos_directos",
    )
    permiso = models.ForeignKey(
        Permiso,
        on_delete=models.CASCADE,
        db_column="permisoid",
        related_name="usuarios_permisos",
    )
    permitido = models.BooleanField(default=True)

    class Meta:
        db_table = "usuariospermisos"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "permiso"],
                name="ux_usuariospermisos_usuario_perm",
            )
        ]

    def __str__(self):
        estado = "permitido" if self.permitido else "bloqueado"
        return f"{self.usuario} - {self.permiso} ({estado})"


class TurnoCaja(models.Model):
    ESTADOS = (
        ("ABIERTO", "ABIERTO"),
        ("CIERRE", "CIERRE"),
        ("CERRADO", "CERRADO"),
    )

    puntopago = models.ForeignKey("PuntosPago", on_delete=models.PROTECT, db_column="puntopago_id")
    cajero    = models.ForeignKey("Usuario", on_delete=models.PROTECT, db_column="cajero_id")

    inicio          = models.DateTimeField(auto_now_add=True)
    cierre_iniciado = models.DateTimeField(null=True, blank=True)
    fin             = models.DateTimeField(null=True, blank=True)

    saldo_apertura_efectivo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # ✅ ventas (snapshot)
    ventas_total      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    ventas_efectivo   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    ventas_no_efectivo= models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # ✅ cierre (comparación usuario vs esperado BD)
    esperado_total     = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    real_total         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # entregado total usuario
    diferencia_total   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    deuda_total        = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # ✅ efectivo (solo informativo)
    efectivo_real       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    diferencia_efectivo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    estado   = models.CharField(max_length=10, choices=ESTADOS, default="ABIERTO")
    creado_en= models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "turnos_caja"

class TurnoCajaMedio(models.Model):
    turno      = models.ForeignKey(TurnoCaja, related_name="medios", on_delete=models.CASCADE, db_column="turno_id")
    metodo     = models.CharField(max_length=50)
    esperado   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    contado    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    diferencia = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        db_table = "turno_caja_medios"
        unique_together = (("turno", "metodo"),)
        
Q2 = Decimal("0.01")
def _to_q2(x: Decimal) -> Decimal:
    return (x or Decimal("0.00")).quantize(Q2)


class CambioDevolucion(models.Model):
    TIPO_CHOICES = (
        ("Cambio", "Cambio"),
        ("Devolucion", "Devolucion"),
    )
    ESTADO_CHOICES = (
        ("Pendiente", "Pendiente"),
        ("Completado", "Completado"),
        ("Cancelado", "Cancelado"),
    )

    cambioid = models.AutoField(primary_key=True, db_column="cambioid")

    productoid = models.ForeignKey(
        "Producto",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        db_column="productoid",
        related_name="cambios"
    )

    cantidad = models.IntegerField()
    proveedorid = models.ForeignKey(
        "Proveedor",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        db_column="proveedorid",
        related_name="cambios"
    )

    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES)
    estado = models.CharField(max_length=50, choices=ESTADO_CHOICES)
    fecha = models.DateField()

    motivo = models.TextField(null=True, blank=True)

    venta = models.ForeignKey(
        "Venta",
        on_delete=models.CASCADE,
        null=True, blank=True,
        db_column="ventaid",
        related_name="cambios"
    )

    detalle = models.ForeignKey(
        "DetalleVenta",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        db_column="detalle_id",
        related_name="cambios"
    )

    class Meta:
        db_table = "cambiosdevoluciones"

    def __str__(self):
        return f"{self.tipo} #{self.cambioid}"

    # -------------------------
    # Helpers internos
    # -------------------------
    @staticmethod
    def _upsert_inventario_delta(sucursal_id: int, product_id: int, delta_cantidad: int):
        """
        DEVOLUCIÓN => delta_cantidad positivo => inventario +delta
        """
        from .models import Inventario

        inv = (
            Inventario.objects
            .select_for_update()
            .filter(sucursalid_id=sucursal_id, productoid_id=product_id)
            .first()
        )
        if inv:
            Inventario.objects.filter(pk=inv.pk).update(cantidad=F("cantidad") + int(delta_cantidad))
        else:
            Inventario.objects.create(
                sucursalid_id=sucursal_id,
                productoid_id=product_id,
                cantidad=int(delta_cantidad)
            )

    @staticmethod
    def _upsert_turno_medio_delta(turno, metodo: str, delta):
        """
        ✅ Nunca revienta si el medio no existe:
        - Tu FK real se llama TURNO (no turnocaja)
        - Si no existe TurnoCajaMedio para ese metodo, lo crea en 0
        - Aplica delta en esperado
        - Si queda negativo, lo deja en 0 (no explota)
        """
        from mainApp.models import TurnoCajaMedio  # evita ciclos

        metodo = (metodo or "").strip().lower()
        delta = _to_q2(delta)

        if not metodo or delta == 0:
            return

        # 1) Buscar registro del medio (LOCK)
        obj = (
            TurnoCajaMedio.objects
            .select_for_update()
            .filter(turno=turno, metodo=metodo)   # ✅ FK real: turno
            .first()
        )

        # 2) Si no existe, crearlo
        if obj is None:
            obj = TurnoCajaMedio.objects.create(
                turno=turno,                      # ✅ FK real: turno
                metodo=metodo,
                esperado=Decimal("0.00"),
                contado=Decimal("0.00"),
                diferencia=Decimal("0.00"),
            )
            obj = TurnoCajaMedio.objects.select_for_update().get(pk=obj.pk)

        # 3) Aplicar delta a esperado
        actual = _to_q2(obj.esperado)
        nuevo = (actual + delta).quantize(Q2)

        # clamp a 0 si queda negativo
        if nuevo < 0:
            nuevo = Decimal("0.00").quantize(Q2)

        obj.esperado = nuevo

        # mantener diferencia coherente si ya existe contado
        try:
            obj.diferencia = (obj.contado - obj.esperado).quantize(Q2)
            obj.save(update_fields=["esperado", "diferencia"])
        except Exception:
            obj.save(update_fields=["esperado"])

    @staticmethod
    def _turno_abierto_para_venta_locked(venta):
        from .models import TurnoCaja
        return (
            TurnoCaja.objects
            .select_for_update()
            .filter(puntopago_id=venta.puntopagoid_id, estado__in=["ABIERTO", "CIERRE"])
            .order_by("-inicio")
            .first()
        )

    # -------------------------
    # ✅ Método principal
    # -------------------------
    @classmethod
    def registrar_devolucion(cls, venta, devoluciones, reintegro_map: dict | None = None):
        """
        ✅ Hace TODO:
        - Registra en cambiosdevoluciones
        - inventario += cantidad devuelta
        - detalleventa.cantidad -= devuelto (para consistencia visual)
        - venta.total -= total devuelto
        - Ajusta turno (ventas_total / ventas_efectivo / ventas_no_efectivo + turno_caja_medios.esperado)
        """
        from .models import DetalleVenta, TurnoCaja

        reintegro_map = reintegro_map or {}

        # 1) calcular total devolución
        total_dev = Decimal("0.00")
        for item in devoluciones:
            det: DetalleVenta = item["detalle"]
            cant = int(item["cantidad"] or 0)
            if cant <= 0:
                continue
            precio = (det.preciounitario or Decimal("0.00"))
            total_dev += (Decimal(cant) * precio)

        total_dev = _to_q2(total_dev)
        if total_dev <= 0:
            return

        now = timezone.localdate()

        # 2) inventario + detalle - + crear registros cambios
        for item in devoluciones:
            det: DetalleVenta = item["detalle"]
            cant = int(item["cantidad"] or 0)
            if cant <= 0:
                continue

            product_id = det.productoid_id
            sucursal_id = venta.sucursalid_id

            # ✅ inventario sube
            cls._upsert_inventario_delta(sucursal_id, product_id, cant)

            # ✅ detalle baja (recomendado)
            DetalleVenta.objects.filter(pk=det.pk).update(cantidad=F("cantidad") - cant)

            # ✅ registrar devolucion
            cls.objects.create(
                venta=venta,
                productoid_id=product_id,
                cantidad=cant,
                tipo="Devolucion",
                estado="Completado",
                fecha=now,
                motivo="Devolución registrada",
                detalle_id=det.pk,
            )

        # 3) venta.total baja
        venta.refresh_from_db()
        nuevo_total = _to_q2((venta.total or Decimal("0.00")) - total_dev)
        if nuevo_total < 0:
            raise ValueError(f"La devolución ({total_dev}) deja el total negativo ({nuevo_total}).")

        venta.total = nuevo_total
        venta.save(update_fields=["total"])

        # 4) ajustar turno
        turno = cls._turno_abierto_para_venta_locked(venta)
        if not turno:
            return

        # ventas_total siempre baja
        TurnoCaja.objects.filter(pk=turno.pk).update(ventas_total=F("ventas_total") - total_dev)

        medio_venta = (venta.mediopago or "").strip().lower()

        # 4A) no mixto => por el mismo medio
        if medio_venta != "mixto":
            cls._upsert_turno_medio_delta(turno, medio_venta, -total_dev)

            if medio_venta == "efectivo":
                TurnoCaja.objects.filter(pk=turno.pk).update(ventas_efectivo=F("ventas_efectivo") - total_dev)
            else:
                TurnoCaja.objects.filter(pk=turno.pk).update(ventas_no_efectivo=F("ventas_no_efectivo") - total_dev)
            return

        # 4B) mixto => usar reintegro_map
        suma = Decimal("0.00")

        for metodo, monto in reintegro_map.items():
            monto = _to_q2(monto)
            if monto <= 0:
                continue
            suma += monto

            cls._upsert_turno_medio_delta(turno, metodo, -monto)

            if (metodo or "").strip().lower() == "efectivo":
                TurnoCaja.objects.filter(pk=turno.pk).update(ventas_efectivo=F("ventas_efectivo") - monto)
            else:
                TurnoCaja.objects.filter(pk=turno.pk).update(ventas_no_efectivo=F("ventas_no_efectivo") - monto)

        suma = _to_q2(suma)
        if suma != total_dev:
            raise ValueError(f"Reintegro mixto ({suma}) != total devolución ({total_dev}).")
