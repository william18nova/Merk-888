# mainApp/forms.py

from django import forms
from .models import Categoria, Cliente, Empleado, Usuario, Sucursal, HorarioCaja, PuntosPago, HorariosNegocio, Producto, Proveedor, Rol, Inventario, PreciosProveedor, PedidoProveedor, DetallePedidoProveedor, Permiso
import re
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from dal import autocomplete
import json
from django.db.models import Exists, OuterRef, Count, Q  # Agrega esta línea
from django.forms import formset_factory, DecimalField, DateField, HiddenInput, TextInput, DateInput
from datetime import date
from django.utils import timezone
from decimal import Decimal

MEDIOS_PAGO = [
    ("efectivo", "Efectivo"),
    ("nequi", "Nequi"),
    ("daviplata", "Daviplata"),
    ("tarjeta", "Tarjeta"),
    ("banco_caja_social", "Banco Caja Social"),
]

telefono_validator = RegexValidator(
    regex=r'^\d{10}$',
    message='El teléfono debe contener exactamente 10 dígitos.'
)

# -----------------------------------------------------------------------------
#  AGREGAR  ▸  SucursalForm
# -----------------------------------------------------------------------------
class SucursalForm(forms.ModelForm):
    """
    • «nombre» permite **cualquier** carácter UTF-8; sólo se valida longitud ≦ 100
    • Se siguen validando duplicados en `clean_nombre`.
    """
    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el nombre de la sucursal",
            "required": True,
        }),
        error_messages={
            "required":   "El nombre es obligatorio.",
            "max_length": "El nombre no puede superar los 100 caracteres.",
        },
    )

    telefono = forms.CharField(
        validators=[telefono_validator],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el teléfono",
            "required": True,
        }),
    )

    class Meta:
        model  = Sucursal
        fields = ("nombre", "direccion", "telefono")
        labels = {
            "nombre":    "Nombre",
            "direccion": "Dirección",
            "telefono":  "Teléfono",
        }
        widgets = {
            "direccion": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa la dirección",
                "required": True,
            })
        }

    # --- unicidad (case-insensitive) ----------------------------------------
    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        if Sucursal.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError("El nombre de la sucursal ya está registrado.")
        return nombre


# -----------------------------------------------------------------------------
#  EDITAR  ▸  SucursalEditarForm
# -----------------------------------------------------------------------------
class SucursalEditarForm(forms.ModelForm):
    """
    Versión para edición:
    · Permite cualquier carácter UTF-8 en el nombre.
    · Sólo comprueba longitud y unicidad (sin disparar error si no se modificó).
    """

    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el nombre de la sucursal",
            "required": True,
        }),
        error_messages={
            "required":   "El nombre es obligatorio.",
            "max_length": "El nombre no puede superar los 100 caracteres.",
        },
    )

    direccion = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa la dirección",
            "required": True,
        }),
        error_messages={"required": "La dirección es obligatoria."},
    )

    telefono = forms.CharField(
        validators=[telefono_validator],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el teléfono",
            "required": True,
        }),
    )

    class Meta:
        model  = Sucursal
        fields = ("nombre", "direccion", "telefono")
        labels = {
            "nombre":    "Nombre",
            "direccion": "Dirección",
            "telefono":  "Teléfono",
        }

    # ───────── validación extra ─────────
    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()

        # si no cambió, aceptamos
        if self.instance and nombre.lower() == self.instance.nombre.lower():
            return nombre

        # si cambió, verificamos unicidad
        existe = Sucursal.objects.filter(
            nombre__iexact=nombre
        ).exclude(pk=self.instance.pk).exists()

        if existe:
            raise forms.ValidationError(
                "El nombre de la sucursal ya está registrado."
            )
        return nombre

class CategoriaForm(forms.ModelForm):
    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa el nombre de la categoría",
            "required"   : "required"
        }),
        error_messages={
            "required"  : "El nombre es obligatorio.",
            "max_length": "No más de 100 caracteres."
        }
    )

    descripcion = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa una descripción (opcional)"
        })
    )

    class Meta:
        model  = Categoria
        fields = ["nombre", "descripcion"]
        labels = {"nombre": "Nombre", "descripcion": "Descripción"}

    # validación de duplicados (nombre UTF-8 sin distinción de mayúsculas)
    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        if Categoria.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError("El nombre de la categoría ya está registrado.")
        return nombre

class EditarCategoriaForm(forms.ModelForm):
    class Meta:
        model  = Categoria
        fields = ("nombre", "descripcion")
        labels = {"nombre": "Nombre", "descripcion": "Descripción"}
        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el nombre de la categoría",
            }),
            "descripcion": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa una descripción (opcional)",
            }),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        qs = Categoria.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                "El nombre de la categoría ya está registrado."
            )
        return nombre

class ClienteForm(forms.ModelForm):
    numerodocumento = forms.CharField(
        label="Número de Documento",
        max_length=30,
        validators=[RegexValidator(r"^\d+$", "Solo dígitos.")],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el número de documento",
            "required": True
        })
    )

    nombre = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el nombre",
            "required": True
        }),
        validators=[RegexValidator(r"^[A-Za-zÁÉÍÓÚáéíóúÑñ\s]+$", "Solo letras y espacios.")],
    )

    apellido = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el apellido",
            "required": True
        }),
        validators=[RegexValidator(r"^[A-Za-zÁÉÍÓÚáéíóúÑñ\s]+$", "Solo letras y espacios.")],
    )

    telefono = forms.CharField(
        max_length=15,
        validators=[RegexValidator(r"^\d{7,15}$", "Entre 7 y 15 dígitos.")],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el teléfono",
            "required": True
        })
    )

    email = forms.EmailField(
        label="Correo Electrónico",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el correo electrónico",
            "required": True
        })
    )

    class Meta:
        model  = Cliente
        fields = ("numerodocumento", "nombre", "apellido", "telefono", "email")

    # ---------- validaciones de unicidad ----------
    def clean_numerodocumento(self):
        num = self.cleaned_data["numerodocumento"]
        if Cliente.objects.filter(numerodocumento=num).exists():
            raise forms.ValidationError("Ya existe un cliente con ese documento.")
        return num

    def clean_telefono(self):
        tel = self.cleaned_data["telefono"]
        if Cliente.objects.filter(telefono=tel).exists():
            raise forms.ValidationError("Ese teléfono ya está registrado.")
        return tel

    def clean_email(self):
        mail = self.cleaned_data["email"]
        if Cliente.objects.filter(email=mail).exists():
            raise forms.ValidationError("Ese correo ya está registrado.")
        return mail

class EditarClienteForm(forms.ModelForm):
    """
    Formulario de edición; se permiten valores iguales al registro
    actual y se valida duplicidad excluyendo `self.instance`.
    """
    class Meta:
        model  = Cliente
        fields = ["numerodocumento", "nombre", "apellido", "telefono", "email"]
        labels = {
            "numerodocumento": "Número de Documento",
            "nombre"         : "Nombre",
            "apellido"       : "Apellido",
            "telefono"       : "Teléfono",
            "email"          : "Correo Electrónico",
        }
        widgets = {
            "numerodocumento": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el número de documento",
                "required": True,
            }),
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el nombre",
                "required": True,
            }),
            "apellido": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el apellido",
                "required": True,
            }),
            "telefono": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el teléfono",
                "required": True,
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el correo electrónico",
                "required": True,
            }),
        }

    # ── validaciones ──
    def clean_numerodocumento(self):
        num = self.cleaned_data["numerodocumento"]
        if not num.isdigit():
            raise forms.ValidationError("Debe contener solo dígitos.")
        dup = (Cliente.objects.filter(numerodocumento=num)
                             .exclude(pk=self.instance.pk)
                             .exists())
        if dup:
            raise forms.ValidationError("Ya existe ese número de documento.")
        return num

    def clean_telefono(self):
        tel = self.cleaned_data["telefono"]
        if not tel.isdigit():
            raise forms.ValidationError("Debe contener solo dígitos.")
        if not 7 <= len(tel) <= 15:
            raise forms.ValidationError("Debe tener entre 7 y 15 dígitos.")
        dup = (Cliente.objects.filter(telefono=tel)
                             .exclude(pk=self.instance.pk)
                             .exists())
        if dup:
            raise forms.ValidationError("Teléfono ya registrado.")
        return tel

    def clean_email(self):
        email = self.cleaned_data["email"]
        dup = (email and Cliente.objects.filter(email=email)
                                        .exclude(pk=self.instance.pk)
                                        .exists())
        if dup:
            raise forms.ValidationError("Email ya registrado.")
        return email
    
class EmpleadoCreateForm(forms.ModelForm):
    """
    «Agregar Empleado»  (estilo ligero + autocompletado)
    ─────────────────────────────────────────────────────
    """

    # ─── validadores simples ───
    text_v  = RegexValidator(r"^[A-Za-z\s]+$", "Solo letras y espacios.")
    doc_v   = RegexValidator(r"^\d{6,10}$",   "6-10 dígitos.")
    tel_v   = RegexValidator(r"^\d{10}$",     "10 dígitos.")

    # ─── campos visibles ───
    numerodocumento = forms.CharField(
        label="Número de Documento",
        validators=[doc_v],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Número de documento",
            "required": True,
        })
    )
    nombre = forms.CharField(
        validators=[text_v],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Nombre",
            "required": True,
        })
    )
    apellido = forms.CharField(
        validators=[text_v],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Apellido",
            "required": True,
        })
    )
    telefono = forms.CharField(
        validators=[tel_v],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Teléfono (10 dígitos)",
            "required": True,
        })
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "Correo electrónico",
            "required": True,
        })
    )
    direccion = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Dirección",
        })
    )
    puesto = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Puesto",
        })
    )

    # autocompletes visibles
    usuario_autocomplete = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar usuario…",
            "autocomplete": "off",
        })
    )
    sucursal_autocomplete = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar sucursal…",
            "autocomplete": "off",
        })
    )

    # ─── ocultos ───
    usuarioid  = forms.ModelChoiceField(
        queryset=Usuario.objects.none(),
        widget=forms.HiddenInput(), required=True
    )
    sucursalid = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        widget=forms.HiddenInput(), required=True
    )

    class Meta:
        model  = Empleado
        fields = ("numerodocumento", "nombre", "apellido",
                  "telefono", "email", "direccion", "puesto",
                  "usuarioid", "sucursalid")

    # ─── queryset dinámico ───
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

        # Sub-consulta para excluir usuarios ya vinculados
        sub = Empleado.objects.filter(usuarioid=OuterRef("pk"))
        libres = Usuario.objects.annotate(has_emp=Exists(sub)).filter(has_emp=False)

        self.fields["usuarioid"].queryset  = libres
        self.fields["sucursalid"].queryset = Sucursal.objects.all()

    # ─── validaciones de unicidad ───
    def clean_email(self):
        e = self.cleaned_data["email"]
        if Empleado.objects.filter(email=e).exists():
            raise ValidationError("El correo ya está en uso.")
        return e

    def clean_numerodocumento(self):
        d = self.cleaned_data["numerodocumento"]
        if Empleado.objects.filter(numerodocumento=d).exists():
            raise ValidationError("Número de documento duplicado.")
        return d

    def clean_telefono(self):
        t = self.cleaned_data["telefono"]
        if Empleado.objects.filter(telefono=t).exists():
            raise ValidationError("Teléfono duplicado.")
        return t

    def clean_usuarioid(self):
        u = self.cleaned_data["usuarioid"]
        if Empleado.objects.filter(usuarioid=u).exists():
            raise ValidationError("Este usuario ya tiene empleado.")
        return u

class EditarEmpleadoForm(forms.ModelForm):
    """
    Formulario de edición con la misma UX que «Editar Usuario».

    • usuario_autocomplete & sucursal_autocomplete son visibles,
      sus FK reales (usuarioid / sucursalid) van ocultas.
    """

    # ─── visibilidad extra (autocompletes) ───
    usuario_autocomplete  = forms.CharField(
        label="Usuario",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escribe para buscar usuario…",
            "autocomplete": "off",
            "required": True,
        })
    )
    sucursal_autocomplete = forms.CharField(
        label="Sucursal",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escribe para buscar sucursal…",
            "autocomplete": "off",
            "required": True,
        })
    )

    # ─── meta ───
    class Meta:
        model  = Empleado
        fields = (
            "numerodocumento", "nombre", "apellido",
            "telefono", "email", "direccion", "puesto",
            "usuarioid", "sucursalid",
        )
        widgets = {
            "usuarioid":  forms.HiddenInput(),
            "sucursalid": forms.HiddenInput(),

            "numerodocumento": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Número de documento",
            }),
            "nombre": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Nombre",
                "required": True,
            }),
            "apellido": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Apellido",
                "required": True,
            }),
            "telefono": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Teléfono (10 dígitos)",
                "required": True,
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control", "placeholder": "Correo electrónico",
                "required": True,
            }),
            "direccion": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Dirección",
            }),
            "puesto": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Puesto",
            }),
        }

    # ─── init ───
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

        # FK querysets
        self.fields["usuarioid"].queryset  = Usuario.objects.filter(
            Q(empleado__isnull=True) | Q(pk=self.instance.usuarioid_id)
        )
        self.fields["sucursalid"].queryset = Sucursal.objects.all()

        # precargar autocompletes
        if self.instance.pk:
            u = self.instance.usuarioid
            s = self.instance.sucursalid
            if u:
                self.fields["usuario_autocomplete"].initial = u.nombreusuario
                self.fields["usuarioid"].initial            = u.pk
            if s:
                self.fields["sucursal_autocomplete"].initial = s.nombre
                self.fields["sucursalid"].initial            = s.pk

    # ─── validaciones de unicidad ───
    def clean_numerodocumento(self):
        v = self.cleaned_data["numerodocumento"]
        if Empleado.objects.filter(numerodocumento=v).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Número de documento duplicado.")
        return v

    def clean_telefono(self):
        v = self.cleaned_data["telefono"]
        if Empleado.objects.filter(telefono=v).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Teléfono duplicado.")
        return v

    def clean_email(self):
        v = self.cleaned_data["email"]
        if Empleado.objects.filter(email=v).exclude(pk=self.instance.pk).exists():
            raise ValidationError("El correo ya está en uso.")
        return v

class HorariosNegocioForm(forms.ModelForm):
    sucursal_autocomplete = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar sucursal…",
            "autocomplete": "off",
        })
    )
    sucursalid = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
    )
    dia_semana   = forms.CharField(required=False, widget=forms.HiddenInput())
    horaapertura = forms.TimeField(required=False, widget=forms.TimeInput(attrs={
        "class": "form-control",
        "type": "time",
    }))
    horacierre   = forms.TimeField(required=False, widget=forms.TimeInput(attrs={
        "class": "form-control",
        "type": "time",
    }))

    class Meta:
        model  = HorariosNegocio
        fields = [
            "sucursalid", "dia_semana",
            "horaapertura", "horacierre"
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # solo sucursales sin horarios existentes
        self.fields["sucursalid"].queryset = (
            Sucursal.objects
            .filter(horariosnegocio__isnull=True)
            .order_by("nombre")
        )

    def clean(self):
        cleaned = super().clean()
        suc     = cleaned.get("sucursalid")
        dia     = cleaned.get("dia_semana")
        ap      = cleaned.get("horaapertura")
        ci      = cleaned.get("horacierre")
        raw     = self.data.get("horarios")

        if not suc:
            self.add_error("sucursalid", "Debe seleccionar una sucursal.")

        if not raw:
            # si no hay lista temporal, requieren campos individuales
            if not dia:
                self.add_error("dia_semana", "Debe seleccionar al menos un día.")
            if not ap:
                self.add_error("horaapertura", "Debe indicar hora de apertura.")
            if not ci:
                self.add_error("horacierre", "Debe indicar hora de cierre.")
            if ap and ci and ap >= ci:
                self.add_error("horacierre", "Hora de cierre debe ser mayor.")
        else:
            # si hay lista, validar solo orden de horas
            if ap and ci and ap >= ci:
                self.add_error("horacierre", "Hora de cierre debe ser mayor.")

        return cleaned

class EditarHorariosSucursalForm(forms.Form):
    """
    Formulario **sólo** para validar la PK de la sucursal y
    comprobar que el payload trae la lista de horarios.

    – El frontend envía:
        {
          "sucursalid": "12",
          "horarios": [
              {"dia": "Lun", "horaapertura": "08:00", "horacierre": "17:00"},
              ...
          ]
        }

    – Los campos de día/hora NO se validan aquí porque llegan
      dentro del array `horarios`; se validan en la vista
      (o al momento de crear los objetos `HorariosNegocio`).
    """

    # ── único campo real ────────────────────────────────────────────
    sucursalid = forms.CharField(required=True)

    # ----------------------------------------------------------------
    # El flag «horarios_present» llega desde la vista para saber
    # si el JSON tenía o no la lista de horarios.
    # ----------------------------------------------------------------
    def __init__(self, *args, horarios_present=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.horarios_present = horarios_present

    # ── validación de sucursal ──────────────────────────────────────
    def clean_sucursalid(self):
        sid = self.cleaned_data["sucursalid"]

        # 1) numérica (si tus PK son int)
        if not sid.isdigit():
            raise forms.ValidationError("ID de sucursal inválido.")

        # 2) existe en BD
        if not Sucursal.objects.filter(pk=sid).exists():
            raise forms.ValidationError("La sucursal no existe.")

        return sid

    # ── validación global ───────────────────────────────────────────
    def clean(self):
        cleaned = super().clean()

        # la vista pasa «horarios_present» en función del JSON
        if not self.horarios_present:
            raise forms.ValidationError("Debe enviar al menos un horario.")

        return cleaned

class HorarioCajaForm(forms.ModelForm):
    puntopago_autocomplete = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar punto de pago…",
            "autocomplete": "off",
        })
    )
    puntopagoid = forms.ModelChoiceField(
        queryset=PuntosPago.objects.filter(horarios_caja__isnull=True).order_by("nombre"),
        widget=forms.HiddenInput(),
        required=True,
    )
    dia_semana   = forms.CharField(required=False, widget=forms.HiddenInput())
    horaapertura = forms.TimeField(required=False, widget=forms.TimeInput(attrs={
        "class": "form-control", "type": "time"
    }))
    horacierre   = forms.TimeField(required=False, widget=forms.TimeInput(attrs={
        "class": "form-control", "type": "time"
    }))

    class Meta:
        model  = HorarioCaja
        fields = ["puntopagoid","dia_semana","horaapertura","horacierre"]

    def __init__(self, *args, horarios_present=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.horarios_present = horarios_present

    def clean(self):
        cleaned = super().clean()
        raw     = self.data.get("horarios", "")
        ap      = cleaned.get("horaapertura")
        ci      = cleaned.get("horacierre")
        dias    = cleaned.get("dia_semana")
        pp      = cleaned.get("puntopagoid")

        if not raw:
            # Validación por campos individuales
            if not dias:
                self.add_error("dia_semana", "Debe seleccionar al menos un día.")
            if not ap:
                self.add_error("horaapertura", "Debe indicar hora de apertura.")
            if not ci:
                self.add_error("horacierre", "Debe indicar hora de cierre.")
            if ap and ci and ap >= ci:
                self.add_error("horacierre", "La hora de cierre debe ser mayor.")
            # Evitar duplicados ya guardados
            if dias and pp:
                for d in dias.split(","):
                    if HorarioCaja.objects.filter(puntopagoid=pp, dia_semana=d).exists():
                        self.add_error("dia_semana", f"Ya existe horario para {d}.")
        else:
            # Si viene JSON, sólo check de orden de horas
            if ap and ci and ap >= ci:
                self.add_error("horacierre", "La hora de cierre debe ser mayor.")

        return cleaned
    
class EditarHorarioCajaForm(forms.Form):
    sucursalid   = forms.CharField(required=True)
    puntopagoid  = forms.CharField(required=True)

    # flags extras
    dia_semana   = forms.CharField(required=False)
    horaapertura = forms.TimeField(required=False)
    horacierre   = forms.TimeField(required=False)

    def __init__(self, *args, horarios_present=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.horarios_present = horarios_present

    # ── validaciones simples ────────────────────────────────
    def clean_sucursalid(self):
        sid = self.cleaned_data["sucursalid"]
        if not sid.isdigit():
            raise forms.ValidationError("ID de sucursal inválido.")
        if not Sucursal.objects.filter(pk=sid).exists():
            raise forms.ValidationError("La sucursal no existe.")
        return sid

    def clean_puntopagoid(self):
        pid = self.cleaned_data["puntopagoid"]
        if not pid.isdigit():
            raise forms.ValidationError("ID de punto de pago inválido.")
        if not PuntosPago.objects.filter(pk=pid).exists():
            raise forms.ValidationError("El punto de pago no existe.")
        return pid

    # ── validación global ──────────────────────────────────
    def clean(self):
        super().clean()
        if not self.horarios_present:
            raise forms.ValidationError("Debe enviar al menos un horario.")
        return self.cleaned_data



    
class ProductoForm(forms.ModelForm):
    
    codigo_de_barras = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ingresa el código de barras"
        })
    )

    # NUEVOS CAMPOS en el form (para que aparezcan en la página)
    impuesto_consumo = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0",
            "placeholder": "Impuesto al consumo"
        })
    )

    icui = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0",
            "placeholder": "ICUI"
        })
    )

    ibua = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0",
            "placeholder": "IBUA"
        })
    )
    
    rentabilidad = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "min": "0",
            "max": "100",
            "placeholder": "Ej: 30 = 30%"
        })
    )

    class Meta:
        model  = Producto
        # Ponlos al final para que en el HTML salgan después de IVA
        fields = [
            "nombre", "descripcion", "precio", "categoria",
            "codigo_de_barras", "iva",
            "impuesto_consumo", "icui", "ibua"
        ]

        labels = {
            "nombre"          : "Nombre",
            "descripcion"     : "Descripción",
            "precio"          : "Precio",
            "categoria"       : "Categoría",
            "codigo_de_barras": "Código de barras",
            "iva"             : "IVA (p. ej. 0.19)",
            "impuesto_consumo": "Impuesto al consumo",
            "icui"            : "ICUI",
            "ibua"            : "IBUA",
        }

        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa el nombre del producto",
                "required": "required"
            }),
            "descripcion": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ingresa la descripción del producto"
            }),
            "precio": forms.NumberInput(attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0",
                "placeholder": "Ingresa el precio",
                "required": "required"
            }),
            "categoria": forms.HiddenInput(),
            "iva": forms.NumberInput(attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0",
                "max": "1",
                "placeholder": "Ingresa el IVA",
                "required": "required"
            }),
        }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._instance_pk = getattr(self.instance, "productoid", None)

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        qs = Producto.objects.filter(nombre__iexact=nombre)
        if self._instance_pk:
            qs = qs.exclude(productoid=self._instance_pk)
        if qs.exists():
            raise forms.ValidationError("El nombre ya está registrado.", code="duplicate")
        return nombre

    def clean_codigo_de_barras(self):
        ean = self.cleaned_data.get("codigo_de_barras")
        if not ean:
            return ean
        qs = Producto.objects.filter(codigo_de_barras=ean)
        if self._instance_pk:
            qs = qs.exclude(productoid=self._instance_pk)
        if qs.exists():
            raise forms.ValidationError("El código de barras ya está registrado.", code="duplicate")
        return ean

    # Si vienen vacíos, guardarlos como 0.00
    def clean_impuesto_consumo(self):
        return self.cleaned_data.get("impuesto_consumo") or Decimal("0.00")

    def clean_icui(self):
        return self.cleaned_data.get("icui") or Decimal("0.00")

    def clean_ibua(self):
        return self.cleaned_data.get("ibua") or Decimal("0.00")


def _s(v):
    """strip seguro: siempre devuelve string."""
    return (v or "").strip()

class ProductoEditarForm(forms.ModelForm):
    
    categoria = forms.ModelChoiceField(
        queryset=Categoria.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
        label="Categoría",
    )

    class Meta:
        model  = Producto
        fields = (
            "nombre",
            "descripcion",

            "precio",
            "precio_anterior",      # ✅ NUEVO (solo lectura)

            "categoria",
            "codigo_de_barras",
            "iva",

            "impuesto_consumo",
            "icui",
            "ibua",
            "rentabilidad",
        )

        labels = {
            "nombre"          : "Nombre",
            "descripcion"     : "Descripción",
            "precio"          : "Precio",
            "precio_anterior" : "Precio anterior",   # ✅

            "codigo_de_barras": "Código de barras",
            "iva"             : "IVA (0 – 1)",

            "impuesto_consumo": "Impuesto al consumo",
            "icui"            : "ICUI",
            "ibua"            : "IBUA",
            "rentabilidad"    : "Rentabilidad (%)",
        }

        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Nombre del producto", "required": True,
            }),
            "descripcion": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "Descripción (opcional)",
            }),

            "precio": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "Precio", "required": True,
            }),

            # ✅ SOLO LECTURA (disabled)
            "precio_anterior": forms.NumberInput(attrs={
                "class": "form-control",
                "step": "0.01",
                "placeholder": "Se llena automáticamente cuando cambia el precio",
                "disabled": True,
            }),

            "codigo_de_barras": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "EAN / código de barras",
            }),
            "iva": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0", "max": "1",
                "placeholder": "IVA (ej. 0.19)", "required": True,
            }),

            "impuesto_consumo": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "Impuesto al consumo (valor $)",
            }),
            "icui": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "ICUI (valor $)",
            }),
            "ibua": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "IBUA (valor $)",
            }),
            "rentabilidad": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0", "max": "100",
                "placeholder": "Ej: 30 = 30%",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["categoria"].queryset = Categoria.objects.all()
        self._pk = self.instance.pk

        if self.instance.pk and self.instance.categoria:
            self.initial["id_categoria_autocomplete_initial"] = self.instance.categoria.nombre

        # ✅ Mostrar 0.00 si viene null en BD
        if self.instance.pk and self.instance.precio_anterior is None:
            self.initial["precio_anterior"] = Decimal("0.00")

    def clean_nombre(self):
        nombre = _s(self.cleaned_data.get("nombre"))
        if not nombre:
            raise ValidationError("Este campo es obligatorio.")
        qs = Producto.objects.filter(nombre__iexact=nombre)
        if self._pk:
            qs = qs.exclude(pk=self._pk)
        if qs.exists():
            raise ValidationError("El nombre ya está registrado.", code="duplicate")
        return nombre

    def clean_codigo_de_barras(self):
        ean = _s(self.cleaned_data.get("codigo_de_barras"))
        if not ean:
            return ean
        qs = Producto.objects.filter(codigo_de_barras=ean)
        if self._pk:
            qs = qs.exclude(pk=self._pk)
        if qs.exists():
            raise ValidationError("El código de barras ya está registrado.", code="duplicate")
        return ean

    def clean_impuesto_consumo(self):
        v = self.cleaned_data.get("impuesto_consumo")
        return v if v is not None else Decimal("0.00")

    def clean_icui(self):
        v = self.cleaned_data.get("icui")
        return v if v is not None else Decimal("0.00")

    def clean_ibua(self):
        v = self.cleaned_data.get("ibua")
        return v if v is not None else Decimal("0.00")

    def clean_rentabilidad(self):
        v = self.cleaned_data.get("rentabilidad")
        v = v if v is not None else Decimal("0.00")
        if v < 0 or v > 100:
            raise ValidationError("La rentabilidad debe estar entre 0 y 100.")
        return v

    
    
    
class ProveedorForm(forms.ModelForm):
    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Nombre del proveedor",
            "required": True,
        })
    )
    
    empresa = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Empresa",
            "required": True,
        })
    )
    telefono = forms.CharField(
        max_length=15,
        validators=[telefono_validator],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Teléfono (7-15 dígitos)",
            "required": True,
        })
    )
    email = forms.EmailField(
        max_length=100,
        required=False,
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "Correo electrónico",
        })
    )
    direccion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "placeholder": "Dirección",
            "rows": 3,
        })
    )

    class Meta:
        model = Proveedor
        fields = ["nombre", "empresa", "telefono", "email", "direccion"]

    # ---------- Validaciones de unicidad ---------- #
    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        if Proveedor.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError("Ya existe un proveedor con este nombre.")
        return nombre

    def clean_telefono(self):
        tel = self.cleaned_data["telefono"]
        if Proveedor.objects.filter(telefono=tel).exists():
            raise forms.ValidationError("Ya existe un proveedor con este teléfono.")
        return tel
    

class EditarProveedorForm(forms.ModelForm):
    """
    Form para editar un proveedor (con la misma lógica de validación que el de agregar).
    """
    class Meta:
        model = Proveedor
        fields = ['nombre', 'empresa', 'telefono', 'email', 'direccion']
        labels = {
            'nombre': 'Nombre',
            'empresa': 'Empresa',
            'telefono': 'Teléfono',
            'email': 'Email',
            'direccion': 'Dirección',
        }
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa el nombre del proveedor',
                'required': 'required'
            }),
            'empresa': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa la empresa',
                'required': 'required'
            }),
            'telefono': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa el teléfono',
                'required': 'required'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa el correo electrónico'
            }),
            'direccion': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa la dirección',
                'rows': 3
            }),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre']
        # Evitar duplicados con otros proveedores
        qs = (Proveedor.objects
             .exclude(pk=self.instance.pk)  # excluye al actual
             .filter(nombre__iexact=nombre))
        if qs.exists():
            raise forms.ValidationError('Ya existe un proveedor con este nombre.')
        return nombre

    def clean_telefono(self):
        telefono = self.cleaned_data['telefono']
        if not telefono.isdigit():
            raise forms.ValidationError('El teléfono debe contener solo dígitos.')
        return telefono

    
class RolForm(forms.ModelForm):
    nombre = forms.CharField(
        max_length=50,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-zÁÉÍÓÚáéíóúÑñ\s]+$",
                message="El nombre solo debe contener letras y espacios."
            )
        ],
        widget=forms.TextInput(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa el nombre del rol",
            "required"   : True,
        }),
        error_messages={
            "required"  : "El nombre es obligatorio.",
            "max_length": "Máximo 50 caracteres.",
        },
    )

    descripcion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa la descripción (opcional)",
            "rows"       : 4
        }),
    )

    class Meta:
        model  = Rol
        fields = ("nombre", "descripcion")
        labels = {
            "nombre"     : "Nombre del Rol",
            "descripcion": "Descripción",
        }

    # --------- unicidad case-insensitive ---------
    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        if Rol.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError("Ya existe un rol con ese nombre.")
        return nombre
    
class RolEditarForm(forms.ModelForm):
    """
    ▸ Formulario para editar un Rol.
    ▸ - Permite conservar el mismo nombre sin lanzar error.
    ▸ - Si el nombre cambia, verifica duplicados (case-insensitive),
         excluyendo el propio registro.
    """

    nombre = forms.CharField(
        label="Nombre del Rol",
        max_length=50,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-zÁÉÍÓÚáéíóúÑñ\s]+$",
                message="El nombre solo debe contener letras y espacios."
            )
        ],
        widget=forms.TextInput(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa el nombre del rol",
            "required"   : True,
        }),
        error_messages={
            "required"   : "El nombre es obligatorio.",
            "max_length" : "El nombre no puede superar 50 caracteres.",
        },
    )

    descripcion = forms.CharField(
        label="Descripción",
        required=False,
        widget=forms.Textarea(attrs={
            "class"      : "form-control",
            "placeholder": "Ingresa la descripción del rol",
            "rows"       : 4,
        }),
    )

    class Meta:
        model  = Rol
        fields = ("nombre", "descripcion")

    # ---------- validación de unicidad ----------
    def clean_nombre(self):
        nombre = self.cleaned_data.get("nombre", "").strip()

        # Si el usuario NO cambió el nombre, lo aceptamos tal cual
        if self.instance and nombre.lower() == self.instance.nombre.lower():
            return nombre

        # Si lo cambió, comprobamos duplicados excluyendo el propio ID
        existe = Rol.objects.filter(
            nombre__iexact=nombre
        ).exclude(pk=self.instance.pk).exists()

        if existe:
            raise forms.ValidationError("Ya existe un rol con ese nombre.")
        return nombre

class InventarioForm(forms.Form):
    """Formulario «liviano»; solo valida datos mínimos."""

    # visibles
    sucursal_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar sucursal…",
            "autocomplete": "off",
        }), required=True)

    producto_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar producto…",
            "autocomplete": "off",
        }), required=False)

    cantidad = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Cantidad",
            "min": "1",
        }), required=False)

    # ocultos
    sucursal  = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        widget=forms.HiddenInput(), required=True)

    productoid = forms.ModelChoiceField(
        queryset=Producto.objects.all(),
        widget=forms.HiddenInput(), required=False)

    class Meta:
        fields = ("sucursal", "productoid", "cantidad")

    # ----------- queryset dinámico -----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        sin_inv = Sucursal.objects.annotate(
            tiene_inv=Exists(
                Inventario.objects.filter(sucursalid=OuterRef("pk"))
            )
        ).filter(tiene_inv=False)

        self.fields["sucursal"].queryset  = sin_inv
        self.fields["productoid"].queryset = Producto.objects.all()

    # ----------- validación cruzada -----------
    def clean(self):
        cd = super().clean()

        if not cd.get("sucursal"):
            self.add_error("sucursal", "Seleccione una sucursal válida.")

        if cd.get("productoid") and not cd.get("cantidad"):
            self.add_error("cantidad", "Indique una cantidad válida.")
        return cd
    

class InventarioFiltroForm(forms.Form):
    """
    Formulario mínimo usado por la vista `InventarioListView`.
    Solo contiene el campo oculto “sucursal” que llega del
    autocompletado (puede ser un PK o la cadena “global”).
    """

    sucursal = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_sucursal(self):
        valor = self.cleaned_data.get("sucursal", "").strip()
        if not valor:                       # → sin filtro (listar nada)
            return ""

        if valor == "global":               # → modo inventario global
            return "global"

        # debe ser un entero correspondiente a una sucursal existente
        if not valor.isdigit():
            raise forms.ValidationError("Sucursal no válida.")
        pk = int(valor)
        if not Sucursal.objects.filter(pk=pk, inventario__isnull=False).exists():
            raise forms.ValidationError("Sucursal no encontrada.")
        return pk

    
class EditarInventarioForm(forms.Form):
    """
    Formulario para editar el inventario de una sucursal con autocomplete.
    """
    # Campo autocompletado de sucursal
    sucursal_autocomplete = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Escriba para buscar sucursal...',
            'autocomplete': 'off',
        })
    )
    # Campo hidden para la ID de la sucursal
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.all(),  # o .none() y luego sobreescribir en la vista
        widget=forms.HiddenInput(),
        required=True
    )

    # Campo oculto con la lista final de productos/cantidades en JSON
    inventarios_temp = forms.CharField(
        required=False,
        widget=forms.HiddenInput()
    )

    def clean_sucursal(self):
        suc = self.cleaned_data.get('sucursal')
        if not suc:
            raise forms.ValidationError('Debe seleccionar una sucursal.')
        return suc

    def clean_inventarios_temp(self):
        # Podrías validar que sea JSON, etc. Por ahora, lo dejas pasar.
        data = self.cleaned_data.get('inventarios_temp', '')
        return data
    

    
class PreciosProveedorForm(forms.Form):
    """Formulario ligero: sólo valida mínimos para el proveedor + precio unitario."""

    # visibles
    proveedor_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar proveedor…",
            "autocomplete": "off",
        }), required=True)

    producto_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar producto…",
            "autocomplete": "off",
        }), required=False)

    precio = forms.DecimalField(
        min_value=0.01,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Ingrese el precio",
            "min": "0.01",
            "step": "0.01",
        }), required=False)

    # ocultos
    proveedor  = forms.ModelChoiceField(
        queryset=Proveedor.objects.none(),
        widget=forms.HiddenInput(), required=True)

    productoid = forms.ModelChoiceField(
        queryset=Producto.objects.all(),
        widget=forms.HiddenInput(), required=False)

    class Meta:
        fields = ("proveedor", "productoid", "precio")

    # ---------- queryset dinámico ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        sin_precios = Proveedor.objects.annotate(
            has_p=Exists(
                PreciosProveedor.objects.filter(proveedorid=OuterRef("pk"))
            )
        ).filter(has_p=False)

        self.fields["proveedor"].queryset  = sin_precios
        self.fields["productoid"].queryset = Producto.objects.all()

    # ---------- validación cruzada ----------
    def clean(self):
        cd = super().clean()

        if not cd.get("proveedor"):
            self.add_error("proveedor", "Seleccione un proveedor válido.")

        if cd.get("productoid") and not cd.get("precio"):
            self.add_error("precio", "El precio debe ser mayor que 0.")
        return cd
    

class EditarPreciosProveedorForm(forms.Form):
    """Valida mínimamente proveedor + precio unitario para edición."""

    proveedor_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar proveedor…",
            "autocomplete": "off",
        }), required=True)

    producto_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar producto…",
            "autocomplete": "off",
        }), required=False)

    precio = forms.DecimalField(
        min_value=0.01,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Ingrese el precio",
            "min": "0.01",
            "step": "0.01",
        }), required=False)

    # ocultos
    proveedor  = forms.ModelChoiceField(
        queryset=Proveedor.objects.all(),
        widget=forms.HiddenInput(), required=True)

    productoid = forms.ModelChoiceField(
        queryset=Producto.objects.all(),
        widget=forms.HiddenInput(), required=False)

    precios_temp = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        fields = (
            "proveedor", "productoid", "precio", "precios_temp",
        )

    # ---------- validación cruzada ----------
    def clean(self):
        cd = super().clean()

        if not cd.get("proveedor"):
            self.add_error("proveedor", "Seleccione un proveedor válido.")

        if cd.get("productoid") and not cd.get("precio"):
            self.add_error("precio", "El precio debe ser mayor que 0.")
        return cd


class PuntosPagoForm(forms.Form):
    """Formulario mínimo; toda la lógica pesada se maneja en la vista."""

    # visibles
    sucursal_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar sucursal…",
            "autocomplete": "off",
        }), required=True)

    nombre = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Nombre del punto de pago…",
        }), required=False)

    descripcion = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Descripción (opcional)…",
        }), required=False)

    dinerocaja = forms.DecimalField(
        min_value=0,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Dinero en caja",
            "step": "0.01",
        }), required=False)

    # ocultos
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        widget=forms.HiddenInput(), required=True)

    class Meta:
        fields = ("sucursal", "nombre", "descripcion", "dinerocaja")

    # ---------- queryset dinámico ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        sin_pp = Sucursal.objects.annotate(
            tiene_pp=Exists(
                PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
            )
        ).filter(tiene_pp=False)

        self.fields["sucursal"].queryset = sin_pp

    # ---------- validación mínima ----------
    def clean(self):
        cd = super().clean()
        if not cd.get("sucursal"):
            self.add_error("sucursal", "Seleccione una sucursal válida.")
        return cd

class PuntosPagoEditarForm(forms.Form):
    """
    Form para 'Editar Puntos de Pago': permite elegir/editar
    la Sucursal con un autocomplete y validamos que la sucursal sea válida.
    """
    # Nuevo campo de autocomplete (mostrará la sucursal actual y permitirá cambiarla)
    sucursal_autocomplete = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Escriba para buscar sucursal...',
            'autocomplete': 'off',
        })
    )
    
    # Sucursal oculta donde guardamos el ID resultante del autocomplete
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
    )

    # Campos opcionales (usados en la parte superior del form para agregar un punto)
    nombre = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nombre del punto de pago...',
        })
    )
    descripcion = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Descripción (opcional)...',
        })
    )
    dinerocaja = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Dinero en caja...',
            'min': '0.00',
            'step': '0.01'
        })
    )

    def __init__(self, *args, **kwargs):
        # Podemos recibir por kwargs un 'initial_sucursal_id'
        # o simplemente usar initial['sucursal'].
        super().__init__(*args, **kwargs)

        # Permitimos "todas" las sucursales, ya que la vista
        # se encargará de filtrar en el autocomplete.
        self.fields['sucursal'].queryset = Sucursal.objects.all()

        # Si existe un initial con 'sucursal_autocomplete' (nombre)
        # o 'sucursal', se puede prefijar. La vista lo hará.

    def clean(self):
        cleaned_data = super().clean()
        sucursal_obj = cleaned_data.get('sucursal')
        if not sucursal_obj:
            self.add_error('sucursal', 'Sucursal no válida.')
        return cleaned_data
    
class UsuarioForm(forms.ModelForm):
    """
    Formulario para crear usuarios con:
      • autocompletado de Rol
      • verificación de nombre único
      • confirmación de contraseña
    """

    # ---------- campo visible del autocomplete ----------
    rol_autocomplete = forms.CharField(
        label="Rol",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escribe para buscar rol…",
            "autocomplete": "off",
            "required": True,
        })
    )

    # ---------- los dos Password ----------
    password1 = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Contraseña",
            "required": True,
        }),
        min_length=6,
        error_messages={"required": "La contraseña es obligatoria."},
    )
    password2 = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirma la contraseña",
            "required": True,
        }),
        min_length=6,
    )

    class Meta:
        model  = Usuario
        fields = ("rolid", "nombreusuario", "password1", "password2")
        labels = {
            "nombreusuario": "Nombre de usuario",
        }
        widgets = {
            "rolid": forms.HiddenInput(),
            "nombreusuario": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre de usuario",
                "required": True,
            }),
        }

    # ---------- init ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rolid"].queryset = Rol.objects.all()

    # ---------- validaciones ----------
    def clean_nombreusuario(self):
        nombre = self.cleaned_data["nombreusuario"].strip()
        if Usuario.objects.filter(nombreusuario__iexact=nombre).exists():
            raise forms.ValidationError("Ese nombre de usuario ya existe.")
        return nombre

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get("password1"), cd.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cd

    # ---------- crear usuario ----------
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user

class UsuarioEditarForm(forms.ModelForm):
    """
    ▸ Edita un usuario existente (rol, username y contraseña opcional).
    ▸ `rol_autocomplete` es el campo visible; `rolid` queda oculto.
    """

    # ─────────── Campos visibles extra ───────────
    rol_autocomplete = forms.CharField(
        label="Rol",
        required=True,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escribe para buscar rol…",
            "autocomplete": "off",
        }),
    )

    contraseña = forms.CharField(
        label="Contraseña",
        required=False,                         # ← dejar vacío ⇒ no cambia
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Contraseña (vacío = sin cambios)",
        }),
    )
    confirmar_contraseña = forms.CharField(
        label="Confirmar contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirmar contraseña",
        }),
    )

    # ─────────── Meta ───────────
    class Meta:
        model  = Usuario
        fields = ("rolid", "nombreusuario")     # los extras se añaden arriba
        labels = {
            "rolid"        : "Rol",
            "nombreusuario": "Nombre de usuario",
        }
        widgets = {
            "rolid": forms.HiddenInput(),
            "nombreusuario": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre de usuario",
                "required": True,
            }),
        }

    # ─────────── init ───────────
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # queryset completo para el <select> oculto
        self.fields["rolid"].queryset = Rol.objects.all()

        # precargar datos del usuario que se está editando
        if self.instance.pk:
            rol_obj = getattr(self.instance, "rolid", None)  # ↲ ya es Rol
            if rol_obj:
                self.fields["rol_autocomplete"].initial = rol_obj.nombre
                self.fields["rolid"].initial            = rol_obj.pk

    # ─────────── validaciones ───────────
    def clean_nombreusuario(self):
        nombre = self.cleaned_data["nombreusuario"].strip()
        qs = (
            Usuario.objects
            .filter(nombreusuario__iexact=nombre)
            .exclude(pk=self.instance.pk)
        )
        if qs.exists():
            raise ValidationError(
                f'El nombre de usuario «{nombre}» ya existe.',
                code="duplicate",
            )
        return nombre

    def clean(self):
        cleaned = super().clean()
        pwd1, pwd2 = cleaned.get("contraseña"), cleaned.get("confirmar_contraseña")
        if pwd1 or pwd2:                       # sólo si al menos uno viene
            if pwd1 != pwd2:
                self.add_error("confirmar_contraseña", "Las contraseñas no coinciden.")
        return cleaned

    # ─────────── save ───────────
    def save(self, commit=True):
        """
        • Actualiza rol, usuario y contraseña (si se indicó).
        • Al ser ForeignKey, podemos asignar el objeto Rol directamente.
          - Si tu campo es int, usa:  usuario.rolid_id = self.cleaned_data["rolid"].pk
        """
        usuario = super().save(commit=False)

        # Rol
        usuario.rolid = self.cleaned_data["rolid"]          # objeto Rol

        # Contraseña (opcional)
        pwd = self.cleaned_data.get("contraseña")
        if pwd:
            usuario.set_password(pwd)

        if commit:
            usuario.save()
        return usuario

class GenerarVentaForm(forms.Form):
    cliente_id = forms.IntegerField(required=False)

    sucursal  = forms.ModelChoiceField(queryset=Sucursal.objects.all(),  label="Sucursal")
    puntopago = forms.ModelChoiceField(queryset=PuntosPago.objects.all(), label="Punto de Pago")

    productos  = forms.CharField(widget=forms.HiddenInput(), required=False)
    cantidades = forms.CharField(widget=forms.HiddenInput(), required=False)

    # ✅ pago simple (compatibilidad / fallback)
    medio_pago = forms.ChoiceField(
        choices=[
            ("nequi", "Nequi"),
            ("efectivo", "Efectivo"),
            ("daviplata", "Daviplata"),
            ("tarjeta", "Tarjeta"),
            ("banco_caja_social", "Banco Caja Social"),
            ("mixto", "Mixto"),
        ],
        widget=forms.HiddenInput(),
        required=False
    )

    # ✅ pagos mixtos: JSON oculto
    pagos = forms.CharField(widget=forms.HiddenInput(), required=False)

    # ✅ NUEVO: efectivo recibido para calcular cambio (hidden)
    efectivo_recibido = forms.CharField(widget=forms.HiddenInput(), required=False)

    # ───── helpers JSON ─────
    def _clean_json(self, field, default="[]"):
        raw = self.cleaned_data.get(field, default)
        if raw in (None, "", "null"):
            raw = default
        try:
            val = json.loads(raw)
        except Exception:
            raise forms.ValidationError(f"{field.capitalize()} inválido.")
        return val

    def clean_productos(self):
        val = self._clean_json("productos", default="[]")
        if not isinstance(val, list):
            raise forms.ValidationError("Productos inválidos.")
        return val

    def clean_cantidades(self):
        val = self._clean_json("cantidades", default="[]")
        if not isinstance(val, list):
            raise forms.ValidationError("Cantidades inválidas.")
        return val

    def clean_pagos(self):
        """
        Espera:
          [
            {"medio_pago":"efectivo","monto":"10000"},
            {"medio_pago":"nequi","monto":"5000"}
          ]
        Retorna SIEMPRE lista.
        """
        val = self._clean_json("pagos", default="[]")
        if val in (None, ""):
            val = []
        if not isinstance(val, list):
            raise forms.ValidationError("Pagos inválidos.")

        cleaned = []
        for it in val:
            if not isinstance(it, dict):
                raise forms.ValidationError("Pagos inválidos.")
            medio = str(it.get("medio_pago", "")).strip().lower()
            monto = it.get("monto", "0")
            cleaned.append({"medio_pago": medio, "monto": monto})
        return cleaned

    def clean_efectivo_recibido(self):
        raw = (self.cleaned_data.get("efectivo_recibido") or "").strip()
        if raw in ("", "null", "None"):
            return Decimal("0")
        try:
            # JS manda "12345.00"
            return Decimal(raw.replace(",", "."))
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Efectivo recibido inválido.")
    
    


class PedidoProveedorForm(forms.Form):
    # -------- PROVEEDOR ----------
    # el visible (solo UI) **NO** debe ser obligatorio en el servidor
    proveedor_autocomplete = forms.CharField(
        required=False,                               #  <──  cambia a False
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Buscar proveedor…",
            "autocomplete": "off"
        })
    )
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.all(),
        widget=forms.HiddenInput(),                   # lo rellena el JS
        required=True
    )

    # -------- SUCURSAL (hidden) ---
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.order_by("nombre"),
        widget=forms.HiddenInput(),                   # idem: lo pone el JS
        required=True
    )

    # -------- FECHA ---------------
    fechaestimadaentrega = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "form-control",
            "min": date.today().isoformat()
        })
    )

    # -------- OTROS ---------------
    comentario = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "placeholder": "Comentario (opcional)",
            "rows": 3
        })
    )
    detalles = forms.CharField(
        widget=forms.HiddenInput(),
        required=True
    )

    # -------- LIMPIEZA GLOBAL -----
    def clean(self):
        cleaned = super().clean()

        # Validar proveedor → si falta, manda el error al campo visible
        if not cleaned.get("proveedor"):
            self.add_error("proveedor_autocomplete", "Seleccione un proveedor.")

        # Validar sucursal
        if not cleaned.get("sucursal"):
            self.add_error("sucursal", "Seleccione una sucursal.")

        # Validar JSON de detalles
        try:
            items = json.loads(cleaned.get("detalles", "[]"))
        except json.JSONDecodeError:
            self.add_error("detalles", "Formato inválido de detalles.")
            return cleaned

        if not items:
            self.add_error("detalles", "Debe agregar al menos un producto.")
        return cleaned


class LineaDevolucionForm(forms.Form):
    """Un input por línea de venta (cantidad a devolver)."""
    detalle_id = forms.IntegerField(widget=forms.HiddenInput)
    devolver   = forms.IntegerField(
        min_value=0, label="Cant.",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "style": "width:5em"}))

MEDIOS_PAGO = (
    ("efectivo", "Efectivo"),
    ("nequi", "Nequi"),
    ("daviplata", "Daviplata"),
    ("tarjeta", "Tarjeta"),
    ("banco_caja_social", "Banco Caja Social"),
)

class DevolucionForm(forms.Form):
    devolver = forms.IntegerField(
        min_value=0,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "form-control text-end devolver-input",
            "style": "width: 5rem;",
            "min": "0",
        })
    )
    detalle_id = forms.IntegerField(widget=forms.HiddenInput())

DevolucionFormSet = formset_factory(DevolucionForm, extra=0)

class PagoMixtoLineaForm(forms.Form):
    medio_pago = forms.CharField(widget=forms.HiddenInput)
    monto = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={
            "class": "pago-monto",
            "step": "0.01",
            "placeholder": "0.00",
            "inputmode": "decimal",
        })
    )

PagoMixtoFormSet = formset_factory(PagoMixtoLineaForm, extra=0)
ReintegroMixtoFormSet = formset_factory(PagoMixtoLineaForm, extra=0)  # mismo shape
    
class EditarPedidoForm(forms.Form):
    proveedor                  = forms.IntegerField(
        widget=forms.HiddenInput()
    )
    proveedor_autocomplete     = forms.CharField(
        label="Proveedor"
    )

    sucursal                   = forms.IntegerField(
        widget=forms.HiddenInput()
    )
    sucursal_autocomplete      = forms.CharField(
        label="Sucursal"
    )

    fechaestimadaentrega       = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Fecha Estimada"
    )

    comentario                 = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Comentario"
    )

    estado                     = forms.ChoiceField(
        choices=PedidoProveedor.ESTADOS,
        label="Estado"
    )

    # — Sólo si estado == "Recibido" —
    monto_pagado               = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Monto Pagado"
    )
    caja_pagoid                = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput()
    )
    caja_pago_autocomplete     = forms.CharField(
        required=False,
        label="Caja de Pago"
    )

    detalles                   = forms.CharField(
        widget=forms.HiddenInput()
    )

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("estado") == "Recibido":
            # Validar monto pagado
            monto = cleaned.get("monto_pagado")
            if monto is None or monto == "":
                self.add_error("monto_pagado", "Debe indicar el monto pagado.")

            # Validar caja de pago
            if not cleaned.get("caja_pagoid"):
                self.add_error(
                    "caja_pago_autocomplete",
                    "Seleccione la caja de pago."
                )

        return cleaned
    
class PermisoForm(forms.ModelForm):
    class Meta:
        model = Permiso
        fields = ['nombre', 'descripcion']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej. Agregar sucursal',
                'maxlength': '50',
                'autocomplete': 'off',
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5,
                'placeholder': 'Descripción breve del permiso (opcional)',
            }),
        }
        labels = {
            'nombre': 'Nombre del permiso',
            'descripcion': 'Descripción',
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        # Unicidad case-insensitive
        if Permiso.objects.filter(nombre__iexact=nombre).exists():
            raise forms.ValidationError("Ya existe un permiso con ese nombre.")
        return nombre
    
class PermisoEditarForm(forms.ModelForm):
    """
    ▸ Form para editar un Permiso.
    ▸ Acepta el mismo nombre si no cambió.
    ▸ Si cambia, valida duplicados (case-insensitive) excluyendo el propio registro.
    """

    nombre = forms.CharField(
        label="Nombre del permiso",
        max_length=50,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-zÁÉÍÓÚáéíóúÑñ0-9\s\-\_]+$",
                message="El nombre solo debe contener letras, números, espacios y - _."
            )
        ],
        widget=forms.TextInput(attrs={
            "class"      : "form-control",
            "placeholder": "Ej. Agregar sucursal",
            "required"   : True,
        }),
        error_messages={
            "required"   : "El nombre es obligatorio.",
            "max_length" : "El nombre no puede superar 50 caracteres.",
        },
    )

    descripcion = forms.CharField(
        label="Descripción",
        required=False,
        widget=forms.Textarea(attrs={
            "class"      : "form-control",
            "placeholder": "Descripción breve del permiso (opcional)",
            "rows"       : 5,
        }),
    )

    class Meta:
        model  = Permiso
        fields = ("nombre", "descripcion")

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()

        # Si no cambió, permitir
        if self.instance and nombre.lower() == (self.instance.nombre or "").lower():
            return nombre

        # Si cambió, validar duplicado
        existe = Permiso.objects.filter(
            nombre__iexact=nombre
        ).exclude(pk=self.instance.pk).exists()

        if existe:
            raise forms.ValidationError("Ya existe un permiso con ese nombre.")
        return nombre
    
class RolPermisoAssignForm(forms.Form):
    """
    Autocompletes visibles + campos ocultos.
    La lista de permisos se manda como JSON en 'permisos_temp'.
    """
    # visibles
    rol_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar rol…",
            "autocomplete": "off",
        }), required=True
    )
    permiso_autocomplete = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar permiso…",
            "autocomplete": "off",
        }), required=False
    )

    # ocultos
    rol = forms.ModelChoiceField(
        queryset=Rol.objects.all(),
        widget=forms.HiddenInput(), required=True
    )
    permisoid = forms.ModelChoiceField(
        queryset=Permiso.objects.all(),
        widget=forms.HiddenInput(), required=False
    )

    class Meta:
        fields = ("rol", "permisoid")

    def clean(self):
        cd = super().clean()
        if not cd.get("rol"):
            self.add_error("rol", "Seleccione un rol válido.")
        return cd

class RolPermisoEditForm(forms.Form):
    """
    En edición, el rol llega por la URL. Aquí lo mantenemos en hidden
    por si validamos algo adicional. El permiso se selecciona por autocomplete.
    """
    permiso_autocomplete = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Escriba para buscar permiso…",
            "autocomplete": "off",
        })
    )

    rol = forms.ModelChoiceField(
        queryset=Rol.objects.all(),
        widget=forms.HiddenInput(),
        required=True,
    )

    permisoid = forms.ModelChoiceField(
        queryset=Permiso.objects.all(),
        widget=forms.HiddenInput(),
        required=False,
    )

    class Meta:
        fields = ("rol", "permisoid")

    def clean(self):
        cd = super().clean()
        if not cd.get("rol"):
            self.add_error("rol", "Rol inválido.")
        return cd


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={
            "class": "form-control",
            "multiple": True,
            "accept": "image/*",
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data]
        return [single(data, initial)] if data else []


class InventarioFotosForm(forms.Form):
    sucursal_autocomplete = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Buscar sucursal...",
            "autocomplete": "off",
        })
    )
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.order_by("nombre"),
        widget=forms.HiddenInput(),
        required=True,
    )
    fotos = MultipleFileField(required=True)

    class Meta:
        fields = ("sucursal", "fotos")

    def clean_fotos(self):
        fotos = self.cleaned_data.get("fotos") or []
        permitidas = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp", "image/gif"}
        if not fotos:
            raise forms.ValidationError("Debes adjuntar al menos una foto.")
        for foto in fotos:
            if getattr(foto, "content_type", "") not in permitidas:
                raise forms.ValidationError(f"Archivo no permitido: {getattr(foto, 'name', 'desconocido')}")
        return fotos

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("sucursal"):
            self.add_error("sucursal_autocomplete", "Selecciona una sucursal valida.")
        return cleaned


class InventarioFotosConfirmarForm(forms.Form):
    sucursal_id = forms.IntegerField(widget=forms.HiddenInput(), required=True)
    items_json = forms.CharField(widget=forms.HiddenInput(), required=True)
    proveedor_json = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_sucursal_id(self):
        sid = self.cleaned_data["sucursal_id"]
        if not Sucursal.objects.filter(pk=sid).exists():
            raise forms.ValidationError("Sucursal invalida.")
        return sid

    def clean_items_json(self):
        raw = (self.cleaned_data.get("items_json") or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise forms.ValidationError("Formato JSON invalido.")

        if not isinstance(data, list) or not data:
            raise forms.ValidationError("No hay productos para procesar.")

        def as_bool(value):
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "si", "sí", "yes"}

        normalizados = []
        for idx, item in enumerate(data, start=1):
            nombre = str((item or {}).get("producto") or (item or {}).get("nombre") or "").strip()
            codigo = str((item or {}).get("codigo_de_barras") or "").strip()
            precio_unitario = str((item or {}).get("precio_unitario") or "").strip()
            precio_unitario_visible = str((item or {}).get("precio_unitario_visible") or "").strip()
            precio_unitario_sin_iva = str((item or {}).get("precio_unitario_sin_iva") or "").strip()
            iva_porcentaje = str((item or {}).get("iva_porcentaje") or (item or {}).get("iva") or "").strip()
            precio_incluye_iva = as_bool((item or {}).get("precio_incluye_iva"))
            precio_iva_calculado = as_bool((item or {}).get("precio_iva_calculado"))
            productoid_raw = (item or {}).get("productoid")
            try:
                productoid = int(productoid_raw) if str(productoid_raw).strip() else None
            except (TypeError, ValueError):
                raise forms.ValidationError(f"Producto invalido en la fila {idx}.")

            try:
                cantidad = int((item or {}).get("cantidad", 0))
            except (TypeError, ValueError):
                raise forms.ValidationError(f"Cantidad invalida en la fila {idx}.")

            if not nombre and not productoid:
                raise forms.ValidationError(f"Producto vacio en la fila {idx}.")
            if cantidad <= 0:
                raise forms.ValidationError(f"La cantidad del producto '{nombre or productoid}' debe ser mayor a 0.")

            normalizados.append({
                "productoid": productoid,
                "producto": nombre,
                "cantidad": cantidad,
                "codigo_de_barras": codigo,
                "precio_unitario": precio_unitario,
                "precio_unitario_visible": precio_unitario_visible,
                "precio_unitario_sin_iva": precio_unitario_sin_iva,
                "iva_porcentaje": iva_porcentaje,
                "precio_incluye_iva": precio_incluye_iva,
                "precio_iva_calculado": precio_iva_calculado,
            })

        return normalizados

    def clean_proveedor_json(self):
        raw = (self.cleaned_data.get("proveedor_json") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise forms.ValidationError("Formato JSON de proveedor invalido.")
        if not isinstance(data, dict):
            raise forms.ValidationError("Proveedor invalido.")

        def as_bool(value):
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "si", "sí", "yes"}

        proveedorid_raw = data.get("proveedorid")
        try:
            proveedorid = int(proveedorid_raw) if str(proveedorid_raw or "").strip() else None
        except (TypeError, ValueError):
            raise forms.ValidationError("Proveedor invalido.")

        return {
            "proveedorid": proveedorid,
            "nombre": str(data.get("nombre") or data.get("proveedor") or "").strip(),
            "empresa": str(data.get("empresa") or "").strip(),
            "telefono": str(data.get("telefono") or "").strip(),
            "email": str(data.get("email") or "").strip(),
            "direccion": str(data.get("direccion") or "").strip(),
            "nit": str(data.get("nit") or "").strip(),
            "factura": str(data.get("factura") or "").strip(),
            "fecha": str(data.get("fecha") or "").strip(),
            "create_if_missing": as_bool(data.get("create_if_missing")),
        }
