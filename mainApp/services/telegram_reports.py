"""Reportes privados acotados; permisos y consultas fijas, sin SQL de la IA."""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from html import escape
from io import BytesIO

from django.utils import timezone

from .expense_reconciliation import CO
from .telegram_finance_workflows import bot, require, report

MAX_ROWS=1000


@dataclass(frozen=True)
class BotDocument:
    name: str
    mime: str
    data: bytes = field(repr=False)


def table(profile,args):
    from mainApp.models import Egreso, Inventario, TurnoCaja, Venta
    kind=args['reporte']; start,end=bot()._date_range(args)
    if (end-start).days>92:
        raise bot().TelegramClarification('Elige como máximo 93 días para el reporte.')
    require(profile,{'pagos':'registrar_egreso','ventas':'visualizar_ventas','inventario':'visualizar_inventarios','cierres':'turnos_caja_dashboard','conciliacion':'registrar_egreso'}[kind])
    branch=bot()._find_branch(args['sucursal']) if args.get('sucursal') else None
    lo=timezone.make_aware(datetime.combine(start,time.min),CO)
    hi=timezone.make_aware(datetime.combine(end+timedelta(days=1),time.min),CO)
    if kind=='conciliacion':
        items,_,_=report(profile,args)
        headers=['Fecha turno','Turno','Fila','Cajero','Sucursal','Concepto','Valor base','Estado','Pagos posibles/vinculados']
        rows=[[r['date'].isoformat(),str(r['turn']),r['index']+1,r['cashier'],r['branch'],r['name'],r['amount'],r['label'],', '.join(str(c['id']) for c in r['candidates'])] for r in items]
    elif kind=='pagos':
        if branch:
            raise bot().TelegramClarification('Pagos no identifica sucursal. No puedo atribuir estos pagos a una sucursal.')
        items=Egreso.objects.select_related('concepto').filter(creado_en__gte=lo,creado_en__lt=hi).order_by('-creado_en','-pk')
        headers=['Pago','Fecha','Concepto','Base','4 x 1.000','Total','Medio','Registró']
        rows=[[str(r.pk),timezone.localtime(r.creado_en,CO).strftime('%Y-%m-%d %H:%M'),r.concepto.nombre,r.monto_base,r.impuesto_4xmil,r.monto,bot().payment_method_label(r.medio_pago),r.registrado_por_nombre] for r in items[:MAX_ROWS+1]]
    elif kind=='inventario':
        items=Inventario.objects.select_related('productoid','sucursalid').order_by('sucursalid__nombre','productoid__nombre','pk')
        if branch: items=items.filter(sucursalid=branch)
        headers=['Producto ID','Producto','Sucursal','Cantidad actual']
        rows=[[str(r.productoid_id),r.productoid.nombre,r.sucursalid.nombre,r.cantidad] for r in items[:MAX_ROWS+1]]
    elif kind=='ventas':
        from .telegram_operations import _sale_branch_scope
        items=_sale_branch_scope(profile,Venta.objects.select_related('clienteid','sucursalid','empleadoid').filter(fecha__range=(start,end))).order_by('-fecha','-pk')
        if branch: items=items.filter(sucursalid=branch)
        headers=['Venta','Fecha','Sucursal','Cliente','Empleado','Total vendido']
        rows=[[str(r.pk),str(r.fecha),str(r.sucursalid),str(r.clienteid),str(r.empleadoid),r.total] for r in items[:MAX_ROWS+1]]
    else:
        items=TurnoCaja.objects.select_related('cajero','puntopago__sucursalid').filter(estado='CERRADO',fin__gte=lo,fin__lt=hi).order_by('-fin','-pk')
        if branch: items=items.filter(puntopago__sucursalid=branch)
        headers=['Turno','Fecha cierre','Cajero','Caja','Sucursal','Ventas guardadas','Diferencia','Deuda']
        rows=[[str(r.pk),timezone.localtime(r.fin,CO).strftime('%Y-%m-%d %H:%M'),str(r.cajero),str(r.puntopago),str(r.puntopago.sucursalid),r.ventas_total,r.diferencia_total,r.deuda_total] for r in items[:MAX_ROWS+1]]
    if len(rows)>MAX_ROWS:
        raise bot().TelegramClarification('El reporte supera 1.000 filas. Reduce las fechas o añade un filtro; no cortaré datos silenciosamente.')
    return headers,rows,start,end


def xlsx(headers,rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book=Workbook(); sheet=book.active; sheet.title='NovaPOS'
    def safe(value):
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):
            return "'"+value
        return value
    sheet.append(headers)
    for row in rows: sheet.append([safe(v) for v in row])
    for cell in sheet[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='173C6B')
    sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width=min(48,max(16,max(len(str(c.value or '')) for c in column)+2))
        for cell in column[1:]:
            if isinstance(cell.value,(Decimal,float)): cell.number_format='#,##0.00'
    output=BytesIO(); book.save(output); return output.getvalue()


def pdf(headers,rows,title):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,LongTable,TableStyle,Spacer
    output=BytesIO(); page=landscape(A4)
    doc=SimpleDocTemplate(output,pagesize=page,leftMargin=24,rightMargin=24,topMargin=24,bottomMargin=28)
    style=ParagraphStyle('cell',fontName='Helvetica',fontSize=7,leading=10,splitLongWords=True)
    def display(value):
        if isinstance(value,Decimal):
            return '$ '+f'{value:,.2f}'.translate(str.maketrans(',.','.,'))
        return str(value if value is not None else '')
    cells=lambda values:[Paragraph(escape(display(v)),style) for v in values]
    data=[cells(headers)]+[cells(row) for row in rows]
    weights=[2.6 if h in {'Concepto','Producto'} else .65 if h in {'Pago','Turno','Fila','Producto ID'} else 1.2 if 'Fecha' in h else 1 for h in headers]
    grid=LongTable(data,colWidths=[(page[0]-48)*w/sum(weights) for w in weights],repeatRows=1,hAlign='LEFT')
    grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#CFE1F5')),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    def footer(canvas,document):
        canvas.setFont('Helvetica',7); canvas.drawString(24,15,f'NovaPOS · reporte privado · página {document.page}')
    doc.title=title; doc.author='NovaPOS'
    doc.build([Paragraph(escape(title),ParagraphStyle('title',fontSize=13,leading=17)),
        Paragraph('Importes en COP. Valores registrados; no son utilidad ni saldo bancario.',style),Spacer(1,10),grid],onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def tool_export(profile,args):
    headers,rows,start,end=table(profile,args)
    title=f"{args['reporte'].capitalize()} · {start} a {end} · {len(rows)} filas"
    if args['reporte']=='inventario': title='Inventario actual · '+str(timezone.localtime(timezone.now(),CO))+' · '+str(len(rows))+' filas'
    kind=args['formato']; content=xlsx(headers,rows) if kind=='xlsx' else pdf(headers,rows,title)
    doc=BotDocument(f"novapos-{args['reporte']}-{start}-{end}.{kind}",
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if kind=='xlsx' else 'application/pdf',content)
    return bot().BotReply(title+'\nDatos registrados, no utilidad ni saldo bancario. Solo para tu chat privado.','exportar_reporte',document=doc)


TOOL_DEFINITIONS=[{'name':'exportar_reporte','description':'Genera un archivo Excel XLSX o PDF privado con datos actuales y permisos verificados. Pagos, ventas, cierres, inventario ACTUAL o conciliación. Hasta 93 días y 1.000 filas; no omite filas. Pagos no admite sucursal. No registra operaciones.',
    'parameters':{'type':'OBJECT','properties':{'reporte':{'type':'STRING','enum':['pagos','ventas','cierres','inventario','conciliacion']},'formato':{'type':'STRING','enum':['xlsx','pdf']},'desde':{'type':'STRING'},'hasta':{'type':'STRING'},'sucursal':{'type':'STRING'}},'required':['reporte','formato']}}]
TOOL_FUNCTIONS={'exportar_reporte':tool_export}
