"""Clasificacion deterministica del catalogo de productos.

El modulo es deliberadamente puro: no importa modelos de Django ni consulta la
base de datos.  Esto permite simular y auditar una recategorizacion completa
antes de aplicar cambios.

Las reglas se evaluan por prioridad.  Las categorias heredadas no se usan como
fuente de verdad, con dos excepciones operativas: FRUVER y S.O.S Colombia.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


MANUAL_REVIEW = "REVISIÓN MANUAL"


@dataclass(frozen=True)
class CategoryDefinition:
    name: str
    description: str


@dataclass(frozen=True)
class Classification:
    category: str | None
    rule: str
    confidence: str


CATEGORY_DEFINITIONS = (
    CategoryDefinition("Frutas y verduras", "Frutas, verduras, tuberculos, hierbas y productos frescos de plaza."),
    CategoryDefinition("Carnes, pescados y embutidos", "Carnes, aves, pescados, mariscos y productos carnicos."),
    CategoryDefinition("Comidas preparadas y congeladas", "Platos listos, precocidos y alimentos congelados salados."),
    CategoryDefinition("Huevos", "Huevos frescos y presentaciones de huevo."),
    CategoryDefinition("Lácteos y refrigerados", "Leches, quesos, yogures y demas alimentos refrigerados."),
    CategoryDefinition("Panadería, arepas y repostería", "Panes, arepas, tortas y productos de panaderia o reposteria."),
    CategoryDefinition("Arroz, granos y pastas", "Arroz, legumbres secas y pastas alimenticias."),
    CategoryDefinition("Cereales, harinas y mezclas", "Cereales, avenas, granolas, harinas y mezclas para preparar."),
    CategoryDefinition("Aceites, azúcar, sal y panela", "Aceites comestibles, azucar, sal, panela y endulzantes basicos."),
    CategoryDefinition("Untables y mermeladas", "Mermeladas, margarinas y cremas dulces o saladas para untar."),
    CategoryDefinition("Salsas, condimentos y sopas", "Salsas, vinagres, condimentos, caldos y sopas."),
    CategoryDefinition("Enlatados y conservas", "Conservas, encurtidos, alimentos enlatados y productos en almibar."),
    CategoryDefinition("Snacks y pasabocas", "Papas, mani, crispetas y otros pasabocas salados."),
    CategoryDefinition("Galletas y tostadas", "Galletas dulces o saladas, wafers y tostadas."),
    CategoryDefinition("Dulces y chocolates", "Dulces, chocolatinas, chicles, caramelos y confiteria."),
    CategoryDefinition("Helados y congelados", "Helados, paletas y postres congelados."),
    CategoryDefinition("Café, chocolate e infusiones", "Cafe, chocolate de mesa, cacao, te e infusiones."),
    CategoryDefinition("Bebidas en polvo", "Refrescos y otras bebidas instantaneas en polvo."),
    CategoryDefinition("Aguas", "Agua potable, mineral, saborizada o con gas."),
    CategoryDefinition("Gaseosas, maltas y energizantes", "Gaseosas, maltas, energizantes y bebidas deportivas."),
    CategoryDefinition("Jugos y bebidas de fruta", "Jugos, nectares y bebidas listas a base de fruta."),
    CategoryDefinition("Cervezas, vinos y licores", "Bebidas alcoholicas, cervezas, vinos y licores."),
    CategoryDefinition("Limpieza del hogar", "Productos y utensilios para lavar superficies, cocina y hogar."),
    CategoryDefinition("Lavandería", "Detergentes, suavizantes y productos para el cuidado de la ropa."),
    CategoryDefinition("Ambientadores y control de plagas", "Ambientadores, insecticidas, repelentes y control de plagas."),
    CategoryDefinition("Cuidado capilar", "Champu, acondicionador, tintes y tratamientos para el cabello."),
    CategoryDefinition("Cuidado dental", "Cremas, cepillos, seda y enjuagues para higiene oral."),
    CategoryDefinition("Cuidado femenino", "Toallas higienicas, tampones y proteccion femenina."),
    CategoryDefinition("Cuidado personal y belleza", "Aseo corporal, cosmeticos, perfumeria y cuidado de la piel."),
    CategoryDefinition("Bebé", "Pañales, alimentacion, aseo y accesorios para bebe."),
    CategoryDefinition("Salud y farmacia", "Medicamentos, primeros auxilios y productos de salud."),
    CategoryDefinition("Mascotas", "Alimentos, higiene y accesorios para mascotas."),
    CategoryDefinition("Papel, desechables y cocina", "Papel, servilletas, desechables y consumibles de cocina."),
    CategoryDefinition("Papelería y escolar", "Cuadernos, escritura, oficina, manualidades y utiles escolares."),
    CategoryDefinition("Ferretería, eléctrico y tecnología", "Ferreteria, electricidad, electronica y accesorios tecnologicos."),
    CategoryDefinition("Hogar y accesorios", "Menaje, decoracion y accesorios generales para el hogar."),
    CategoryDefinition("Ropa interior", "Brasieres, ropa interior y accesorios de brasier."),
    CategoryDefinition("Trajes de baño", "Vestidos, trajes, pantalonetas y salidas de baño."),
    CategoryDefinition("Piscina y acuáticos", "Flotadores y accesorios para piscina o natacion."),
    CategoryDefinition("Cigarrillos y encendedores", "Cigarrillos, tabaco, encendedores y fosforos."),
    CategoryDefinition("Recargas y servicios", "Recargas, pines y servicios no inventariables."),
    CategoryDefinition("S.O.S Colombia", "Productos y apoyos del programa S.O.S Colombia."),
    CategoryDefinition("Sin categoría", "Productos pendientes de una clasificación más específica."),
)


CATEGORY_NAMES = tuple(definition.name for definition in CATEGORY_DEFINITIONS)


# Alias observados en el catálogo real. Son deliberadamente específicos: su
# objetivo es recuperar nombres legacy, abreviados o con errores ortográficos
# sin convertir una marca o ingrediente genérico en una señal peligrosa.
_CATALOG_ALIAS_RULES = (
    (
        "Frutas y verduras",
        "produce",
        (
            "AJO M E", "PULPA DE LA CASA", "PULPAS DE FRUTAS", "BANDEJA CHAMPINONES",
            "FRIJOS VERDE", "MANZANA PEQUENA", "PEREJIL LISO", "FRUTAS NATURALES MORA",
            "FR PAQUETE DE YERBABUENA", "FR COLIFLOR", "FR YUCAPELADA",
            "FR PAPA LAVADA", "FR PAQUETE PAPA LAVADA", "PAQUETE PAPA PASTUSA",
            "FR PAQUETE HINOJO",
        ),
    ),
    (
        "Piscina y acuáticos",
        "aquatic",
        ("GORROS DE PISCINA",),
    ),
    (
        "Trajes de baño",
        "swimwear",
        ("BLUSA DE BANO",),
    ),
    (
        "Cigarrillos y encendedores",
        "tobacco",
        ("ROTHMANS", "LUCKI STRIKE"),
    ),
    (
        "Recargas y servicios",
        "services",
        ("IMP CONSUMO BOLSA PLASTICA",),
    ),
    (
        "Mascotas",
        "pets",
        (
            "SUPER CAN", "DELICACHORROS", "ALIMENTO PARA PECES", "INCROS VITAL",
            "DOW CHOW", "NUTRIS CACHORRO", "GALLETAS WAU",
        ),
    ),
    (
        "Bebé",
        "baby",
        ("ARRURU TOALLITAS", "ARRURU COLONIA", "ARRURU JABON"),
    ),
    (
        "Salud y farmacia",
        "health",
        (
            "AMPICILINA", "BACTODERM", "BACTRODERM", "BONFIEST", "COLIK FORTE",
            "DURAFLEX", "FORTE MUSCULAR", "GASTROFAST", "GASTRUM", "GAVISCON",
            "LOMOTIL", "LORATADINA", "MAREOL", "PRUEBA DE EMBARAZO", "CONGESTEX",
            "CEBION", "ESPALADRAPO", "MUESTRAS LABORATORIO", "KOLA GRANULADA MK",
            "ACETATO DE ALUMINIO", "GAVISCONX",
        ),
    ),
    (
        "Cuidado capilar",
        "hair_care",
        (
            "CHAMPIOJO", "SEDALDUO", "NUTRIVELA", "REGENERADOR CAPILAR",
            "KERATINA DGES", "PANTANE TRATAMIENTO", "PEINE PIOJO",
            "ACEITE CAPILAR", "HERBAL ESSENCES", "ALMA ROMERO Y CEBOLLA TRATAMIENTO",
            "NUTRIBELA10 TERMOPROTECCION", "GEL XTREME",
        ),
    ),
    (
        "Cuidado dental",
        "dental_care",
        ("CEPILLO EXTRA CLEAN",),
    ),
    (
        "Cuidado personal y belleza",
        "personal_care",
        (
            "SPEEDSTICK", "LADY CLINICAL", "COPITOS", "RUBOR", "POMYS",
            "POMITOS ESENCIAL", "LIMA PARA UNAS", "REMOVEDOR LANDER",
            "BRILLO TAPA DE BOLA", "BRILLO CUPCAKES", "BRILLO FIGURA ANIMALES",
            "MILEFIORE CREMA PARA MANOS", "DR LOOK ROLON", "TOALLITAS HUMEDAS",
            "PANO HUMEDO", "AQUA NATURAL TOALLAS HUMEDAS", "CREMA EXFOLIANTE",
            "LADY SS COMPLETE", "PINZA NEGRA CABELLO", "PINZAS PARA CABELLO",
            "DONAS PARA PEINAR", "PINZA BRILLANTES", "SET COLLAR PINZAS",
            "BANDAS SURTIDA", "ACEITE DE ALMENDRAS", "MANTECA DE CACAO",
            "PROTEX VITAMINA E", "DESEO MANZANA VERDE", "PIOJITOS",
        ),
    ),
    (
        "Lavandería",
        "laundry",
        (
            "AK 1 DETER", "DETER LIQ", "DET LIQUIDO", "SUPER RIEL", "GOLIAT DET",
            "EXTRA QUITA MANCHAS", "ARUMATEL SUAVISANTE", "AZULK BLANCO",
            "MIR COCO BARRA", "BLANCOX ROPA", "CLOROX QUITAMANCHAS COLORES",
            "LIMPIDO ROPA COLOR", "VEL ROSITA", "PURO HORTENSIAS Y FLORES BLANCAS",
        ),
    ),
    (
        "Limpieza del hogar",
        "household_cleaning",
        (
            "TOP ESPONJILLA", "TERGO LAVANDA", "MICROFIBRA", "DESENGRASANTE",
            "SODA CAUSTICA", "LOZA CREM", "ESPONJILLON", "ESPONJILLA BOMBRIL",
            "ESPONJILLA ACERO", "LIMPIAPISOS", "DISTREBOL PORCELIN", "BIOVARSOL",
            "GUANTE S DOMESTICOS", "GUANTE LA NEGRITA", "LIMPIA VENTANA",
            "LIMPIA JUNTAS", "BRILLA KING", "FIBRA ABRASIVA", "BRUSH SANITARY",
            "CHUPA SANITARIA", "CEPILLO MANO", "CEPILLO LIMPIA JUNTAS",
            "LIMPIAVIDRIO", "DESTAPA TUBERIAS", "BOLSA PARA BASURA NEGRA",
            "TIDY HOUSE PANO", "DERSA LAVALOZA", "LAVALOZA DERSA", "LAVALOZA VITAMINA E",
            "BLANCOX LOZACREM VITAMINA E", "IDEAL JABON LAVAPLATOS",
        ),
    ),
    (
        "Ambientadores y control de plagas",
        "air_and_pest",
        (
            "KILLER EXTERMINADOR", "ESPIRAL KATORI", "KILLING BAIT", "FRESH CITRUS",
            "POWER CONTRATAQUE", "PLUGINS ACEITE REPUESTO", "GEL MORA RADIANTE",
        ),
    ),
    (
        "Papelería y escolar",
        "stationery",
        (
            "BOLCK CARTA", "BOLSA PRIMAVERA", "BOLSA DE REGALO", "COLORES Q NOTA",
            "COLORES CLICKIT", "COMPAS METALICO", "CONFETI", "ESCARCHA", "HILO COBRA",
            "HOJAS CARTA", "HOJAS OFICIO", "MICROPUNTA", "REGLA UNIVERSAL",
            "REGLA FLEXIBLE", "RESMA CARTA", "TARJETA REGALO", "GRAPA COSEDORA",
            "VINILOS PARCHESITOS", "IMAGENES COLORES", "CINTA SEGEL", "ARCILLA NATURAL",
            "SOBRE FIFA PANINI", "ALBUN WORLD CUP", "HUELLERO", "BOLIGRA NEGRO",
            "ESCARCHA", "ESCARCHAXTUBO", "LLUVIA DE SOBRES", "MARACADOR PERMANENTE",
            "MARACDOR PERMANENTE", "KILOMETRICO INJOY", "CARTUCHERAS",
        ),
    ),
    (
        "Ferretería, eléctrico y tecnología",
        "hardware_tech",
        (
            "TOMA CORRIENTE", "CARDADOR CELULAR", "CARGADOR 14 PRO MAX", "AUDIFONOS",
            "MEMORIA PLUS USB", "CINTA METRICA", "CLAVIJA", "ESTENSION ELECTRICA",
            "SUPER GLUE", "SUPER GLUEX", "SUPER PEGA INFINITA", "SUPERGOTA INSTANTANEO",
            "ACEITE 3 EN 1",
        ),
    ),
    (
        "Papel, desechables y cocina",
        "paper_disposables",
        (
            "BOLSA MANIJA PLASTICA", "BOLSA MANIGUETA", "CUCHARAS SOPERAS TAMI",
            "TENEDORES TAMI", "VACAN VASO", "VASO VACAN", "BOLSA DE PAPEL",
            "FAMILIA SEERVILLETAS", "PAPEL HIGENICO FAMILIA", "PAPEL HIGENICO FAMILIA",
            "BOLSA REUTILIZABLE", "PORTACOMIDA DESECHABLE", "TENEDOR TAMI",
            "CUCHARA TAMI",
        ),
    ),
    (
        "Hogar y accesorios",
        "home_accessories",
        (
            "BALDE", "COLADOR", "CUBIERTOS METALICOS", "GRATER RALLADOR",
            "CARBON DEL HUILA", "PAQUETE DE BOMBA METALICA", "PAQUETE BOMBA SURTIDO",
            "MOLDE PARA LASAGNA", "PORTA CEPILLO", "COLADORES", "TABLA DE PICAR",
            "PAQUETE DE BOMBA SURTIDO PASTEL",
        ),
    ),
    (
        "Dulces y chocolates",
        "candy",
        (
            "DONA GUAYABA", "ADAMS", "SPLOT ACIDO", "TRIIDENT", "M M COCOLATE",
            "MEGA BALL", "CHOCMELOS", "MILKYWAY", "SNICKERS", "PASTILLAS CHAO",
            "ITALO ALMENDRA", "CHOCODISCK", "MECHAS LOCAS", "MILLOS SURTIDO",
            "QUIPITOS", "TIC TAC", "VELENO DONA GUAYABA", "LONJA BOCADILLO",
            "PANELITAS OJITOS", "GELIFRUIT", "ALOHA", "HERPOS", "SUPER COCO TURRON",
            "SUPERHIPERACIDO", "BOTELLITA CHICLESITOS", "POLVO ACIDO", "BOCA RICOS BOCADILLO",
            "CREMA MUUU CON AVELLANAS", "OJITOS RELLENOS", "CHOMELLS", "GOL MASHMELO",
            "HUEVITO FRITO", "HUEVITO POP", "CHILINDRINAS CHUPETAS", "CHILINDRINA REDONDAS",
            "ALMENDRAS CUBIERTAS CON CHOCLATE", "BESO DE AMOR", "CHICLE HAMBURGUESA",
            "BONBONBUM MANZANA POSTOBON", "LOKINO MINIS FRUTAL", "CHOCO BREAK FRUTAL",
            "COFFEE DE LIGHT", "SUPER ACIDO METRO", "ITALO ALMENDRAS", "CHUPI WOM",
            "REVOLCON HIPER ACIDO", "MILLOW CORAZON", "LONJA DE GUAYABA", "GOL ORIGINAL",
            "JELLY CIOSO HUEVO FRITO", "HUEVOS FRITOS", "BRICKS HUEVO SURTIDO",
            "HUEVO BOMBERO", "GELATINA DE PATA", "MANJAR DE AREQUIPE Y BOCADILLO",
            "BIANCHI CARAMELO MANI", "JUMBO MANI", "BIANCHI BARRA", "BIANCHI CHOCO MANI",
            "BIANCHI CRUKIES MANI", "MR BROWN MANI", "TRULULU HELADO DE FRESA",
            "BONBON BUM CON TAJIN",
        ),
    ),
    (
        "Galletas y tostadas",
        "biscuits_toast",
        (
            "RITZ", "ALFAJOR", "CALADOS", "PIAZZA BANDEJA", "ROSQUITAS",
            "SANDUCHE FRESA", "SANDUCHE VAINILLA", "PAQUETE DE CUCAS",
            "BRIDGE MOUSSE", "MORENITAS", "GUDIZ VAINILLA", "NUTELLA B READY",
            "BRIDGE AREQUIPE", "CAPRI VAINILLA",
        ),
    ),
    (
        "Panadería, arepas y repostería",
        "bakery",
        (
            "BIMBOLETES", "MARINELA MANCHITAS", "PEKITAS", "PINGUINOS FLOW",
            "PINGUINOS IKIS", "RAMITO", "SUBMARINO DE FRESA", "SUBMARINO FRESA",
            "SUBMARINO DE MORA", "AREPA CON SALCHICHON", "BIMBO SUPER HAMBURGUESA",
            "SUBMARINO DE AREQUIPE", "SUBMARINO AREQUIPE", "MARINELA PIOLO",
            "AREPAS CON QUESO",
        ),
    ),
    (
        "Snacks y pasabocas",
        "snacks",
        (
            "CHICHARRONAS", "TOSINETAS", "TROCITOS SABOR BBQ", "TAJADITAS PLATANO",
            "MANIMOTO", "PAQUETE DE PAPA SURTIDO", "BARY SNACKS", "TOCIRICAS",
            "TORTILLA DE MAIZ", "LA ESPECIAL MIX PASAS", "LA ESPECIAL MEZCLA NUECES",
            "CHACHOS NACHOS", "RODELIS ROSQUITAS", "SEMILLAS DE GIRASOL X 200",
            "SEMILLAS DE GIRASOL X 200GR",
            "GOLPE MAYONESA", "MARGARITA ONDULADAS MAYONESA", "MARGARITA FRANCESITAS",
            "YUPI RIZADAS MAYONESA", "YUPI TOSTI EMPANADA", "YUPI TOSTI PIZZA",
            "RECETA CLASICA ALITAS", "TODO RICO PICADA", "TAJAMIEL PLATANO",
            "PLATANO VERDE X 140GR", "PLATANO MADURO X 140GR",
            "RAMO TOSTACOS", "PALOMITA X CARAMELO QUESO",
        ),
    ),
    (
        "Comidas preparadas y congeladas",
        "prepared_frozen",
        (
            "PERRO CALIENTE", "PAPA ALA FRANCESA", "RECONGELISTO", "PALMITO DE CANGREJO",
            "EMBUELTO DE MAZORCA", "ZENU PAPAS EN CASCO", "FRITTERS PAPA FRITA",
            "CALYPSO PAPAS FRITAS", "CALYPSO MAIZ DULCE",
        ),
    ),
    (
        "Enlatados y conservas",
        "canned_preserved",
        (
            "CHAMPINONEZ TAJADOS", "ZENU FRJOLES", "BARY MAIZ TIERNO",
            "DURAZNO EN MITADES", "AL FRESCO CEREZAS", "CEREZAS DUGRE",
            "ALCAPARRAS VEGETALES", "PIMENTONES ASADOS", "CONSERVAS DE PESCADO",
            "ZENU ENSALADAS DE VEGETALES", "MAICITOS DULCES", "MAIZ DULCE",
            "VAN CAMPS ACEITE DE OLIVA",
        ),
    ),
    (
        "Carnes, pescados y embutidos",
        "meat_fish",
        (
            "COSTILLAS RANCHERA", "MOJARRA", "MUSLO Y CONTRAMUSLO", "ALAS PLAZA",
            "PLAZA BANDEJA PIERNAS", "PLAZA RABADILLAS", "PLAZA COLOMBINA DE ALA",
            "POSTA DE BASA", "BUTIFARRA", "CABANO RANCHERO", "ZENU SALCHICHAS",
        ),
    ),
    (
        "Lácteos y refrigerados",
        "dairy",
        (
            "LECHERA ORIGINAL", "LECHERA", "CREMA CHANTILLY", "ALPINA AVENA",
            "LATTI YOGURT", "ALMONDEE BEBIDA DE ALMENDRAS", "CELEMA BEBIDA DE ALMENDRAS",
            "ALQUERIA GELATINA", "ALQUERIA FORTIKIDS GELATINA", "ARTESANAL FLAN DE AREQUIPE",
            "DE LA CUESTA AVENA", "NESTLE MILO X 180ML", "ALPINITO MAX GALLETA",
            "BONYURT OREO",
        ),
    ),
    (
        "Cereales, harinas y mezclas",
        "cereals_flour",
        (
            "KELLOGGS", "KELLOGG S", "FROOT LOOPS", "CHOCOKRISPIS", "CHOCOCRISPIS",
            "PANCAKES", "POLVO PARA HORNEAR", "CORONA BROWNIES", "QUICKSY MEZCLA",
            "COCO CABELLO DE ANGEL", "QUINUA", "CUCHUCO", "SOYA X 500", "MAZAMORRA",
            "NUECES DE BRASIL", "ALMENDRAS X 100", "AVENA INSTANTANEA",
            "GELATINAS DE SABORES", "COMBO PASAS COCO", "COCO NATURAL",
            "TOSH GRANOLA", "QUICKSY TORTA", "AJONJOLI TOSTADO", "CHIPS DE CHOCOLATE",
            "COMESTIBLES PIPE COCO", "CAROLINA CIRUELA SIN SEMILLA",
            "LA GRANJA PAISA COCO NATURALX", "BATI CREMA",
        ),
    ),
    (
        "Arroz, granos y pastas",
        "rice_grains_pasta",
        (
            "SPAGUETTI", "MACARRONCITO", "EL ESTIO FRIJOL", "FRIJOL MARITZA",
            "EL PORVENIR FRIJOL", "EL PORVENIR ARVEJA", "DORIA LASAGNA", "AJINOMEN",
            "DORIA MACARRONES CON QUESO",
        ),
    ),
    (
        "Salsas, condimentos y sopas",
        "sauces_seasoning",
        (
            "MONIK CONDIMENTOS", "MOSTANEZA", "ZAFRAN", "AJI EN POLVO", "AJO EN POLVO",
            "GENGIBRE EN POLVO", "AJO EN PASTA", "AJI CRIOLLO", "ESENCIA SABOR A VAINILLA",
            "GUACAMOLE VASO", "NUEZ MOSCADA", "TOPING MEDITERRANEO", "ALBAHACA PICADA",
            "ROMERO ENTERO", "CANILA MOLIDA", "LAUREL HOJA", "GUASCA X",
            "BASE CHOP SUEY", "YERBERITO ALBAHACA", "ANIS ESTRELLADO",
        ),
    ),
    (
        "Aceites, azúcar, sal y panela",
        "oil_sugar_salt",
        (
            "ACEITE GOURMET FAMILIA MULTIUSOS", "GOURMET FAMILIA",
            "GOURNET FAMILIA",
        ),
    ),
    (
        "Untables y mermeladas",
        "spreads",
        ("SYRUP", "AUNT JEMINA", "RAMA MULTIUSOS", "LA BUENA CREMOSA"),
    ),
    (
        "Café, chocolate e infusiones",
        "coffee_infusion",
        (
            "CHOCOLISTO", "CORONA BOGOTANO", "JUAN VALDEZ", "HINDU FRUTOS",
            "MORINGA", "MANZANILLA", "FLOR DE JAMAICA", "CALENDULA", "BOLDO",
            "ACACIA DE LA INDIA", "SEN X", "CHOCOLATE CORONA", "CHOCOLATE QUESADA",
            "CORONA CHOCOLATE", "MILO ACTI GO",
            "CHOCOLATE CRUZ", "LUKER TRADICIONAL", "MEZCLAO YA PANELA Y CAFE",
            "CHOCOLATE AROMA", "NESQUIK", "BUEN DIA CAFE", "TOSTAO CAFE TOSTADO",
            "INSTACREM", "LYNE CLASICO",
        ),
    ),
    (
        "Bebidas en polvo",
        "powdered_drinks",
        ("SUN TEA",),
    ),
    (
        "Aguas",
        "water",
        ("CRISTAL AGUA", "FULL AQUA NATURAL", "BOLSA AGUA CONGELADA"),
    ),
    (
        "Gaseosas, maltas y energizantes",
        "soft_drinks",
        (
            "CANADA DRY", "H2OH", "SPARTAN ENERGY", "ELECTROLIFE", "FLASLYTE",
            "FLASHLITE", "MPER ENERGY",
        ),
    ),
    (
        "Jugos y bebidas de fruta",
        "fruit_drinks",
        ("COOL DRINKS", "ZUMO LIMON"),
    ),
    (
        "Cervezas, vinos y licores",
        "alcohol",
        (
            "TAPA ROJA", "BUDWEISER", "HEINEKEN", "MILLER LITE", "JW RED LABEL",
            "COCTEL TINTO DE VERANO", "REFAJO", "LOS CUATES", "BLACKYWHITE",
        ),
    ),
    (
        "Helados y congelados",
        "ice_cream",
        (
            "HIELO EN CUBOS", "BOLIS X UNI", "MONTESION HIELO", "PALETA TOSH",
            "GALLETA CON HELADO", "VASO ALOHA KOLA",
        ),
    ),
)


# Estos 88 IDs fueron revisados manualmente al conservar el catalogo de ropa y
# piscina. Los overrides evitan depender de errores ortograficos o nombres muy
# genericos como "TOP NIÑA" y "CONJUNTO NIÑA".
_AQUATIC_PRODUCT_IDS = frozenset({2478, 5096, 5097, 5098, 5099, 5100, 5101})
_SWIMWEAR_PRODUCT_IDS = frozenset(
    {
        5087, 5197, 5347, 5348, 5349, 5350, 5351, 5352, 5353, 5354,
        5355, 5356, 5357, 5358, 5359, 5360, 5361, 5362, 5363, 5364,
        5619, 5620, 6706, 7487, 7488, 7489, 7490, 7491, 7492, 7493,
        7498, 8033, 8534, 8538, 8540, 8541, 8542, 8543, 8544, 8546,
        8547, 8548, 8549, 8551, 8552, 8553, 8554, 8556, 8557, 8558,
        8559, 8560, 8567, 25062112,
    }
)
_UNDERWEAR_PRODUCT_IDS = frozenset(
    {
        4539, 4540, 4541, 4549, 4550, 4551, 4561, 4562, 4563, 4564,
        4565, 7680, 8561, 8562, 8563, 8564, 8565, 8566, 8569, 8570,
        5333, 5334, 5482, 5483, 5485, 5486, 5487,
    }
)


def normalize_product_text(value) -> str:
    """Devuelve texto ASCII, en mayusculas y con espacios canonicos."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    # Muchos nombres legacy pegan la presentación al producto (HITX200ML,
    # AREQUIPEX220GR). Separar esa X mejora las coincidencias sin tocar IDs.
    text = re.sub(r"(?<=[A-Z])X(?=\d)", " X", text)
    text = re.sub(
        r"(?<=[A-Z0-9])X(?=(?:UNI|UND|UNIDAD|UNIDADES|GR|KG|ML|CM|CC|LT)(?:\b|$))",
        " X ",
        text,
    )
    return " ".join(text.split())


def _has_any(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {text} "
    return any(f" {normalize_product_text(phrase)} " in padded for phrase in phrases)


def _has_all(text: str, phrases: Iterable[str]) -> bool:
    return all(_has_any(text, (phrase,)) for phrase in phrases)


def _result(category: str, rule: str, confidence: str = "high") -> Classification:
    return Classification(category=category, rule=rule, confidence=confidence)


def _coerce_product_id(product_id):
    if isinstance(product_id, bool):
        return product_id
    try:
        return int(product_id)
    except (TypeError, ValueError):
        return product_id


def classify_product(
    product_id,
    name,
    description="",
    current_category_name="",
    plaza_destination_ids=frozenset(),
) -> Classification:
    """Clasifica un producto sin consultar ni modificar la base de datos.

    ``confidence`` usa ``high`` para una señal inequivoca, ``medium`` para una
    familia de palabras y ``review`` cuando no existe evidencia suficiente.
    """

    product_id = _coerce_product_id(product_id)

    if product_id in _AQUATIC_PRODUCT_IDS:
        return _result("Piscina y acuáticos", "manual_override:protected_aquatic")
    if product_id in _SWIMWEAR_PRODUCT_IDS:
        return _result("Trajes de baño", "manual_override:protected_swimwear")
    if product_id in _UNDERWEAR_PRODUCT_IDS:
        return _result("Ropa interior", "manual_override:protected_underwear")

    normalized_destinations = {_coerce_product_id(value) for value in (plaza_destination_ids or ())}
    if product_id in normalized_destinations:
        return _result("Frutas y verduras", "trusted_source:plaza_destination")

    current = normalize_product_text(current_category_name)
    if _has_any(current, ("S O S", "SOS COLOMBIA")):
        return _result("S.O.S Colombia", "trusted_current_category:sos")
    if _has_any(current, ("FRUVER",)):
        return _result("Frutas y verduras", "trusted_current_category:fruver")

    name_text = normalize_product_text(name)
    description_text = normalize_product_text(description)
    text = " ".join(part for part in (name_text, description_text) if part)
    if not text:
        return _result("Sin categoría", "fallback:empty_name", "low")

    for alias_category, alias_rule, alias_phrases in _CATALOG_ALIAS_RULES:
        if _has_any(name_text, alias_phrases):
            return _result(alias_category, f"catalog_alias:{alias_rule}")

    # Servicios y productos regulados tienen prioridad sobre palabras de marca
    # que tambien pueden parecer alimentos o articulos de hogar.
    if _has_any(
        text,
        (
            "RECARGA", "RECARGAS", "PIN VIRTUAL", "PAQUETE DE MINUTOS",
            "SERVICIO DE DOMICILIO", "DOMICILIO",
        ),
    ):
        return _result("Recargas y servicios", "keyword:services")
    if _has_all(text, ("VELON", "ENCENDEDOR")):
        return _result("Hogar y accesorios", "exception:candle_with_lighter")
    if _has_any(
        text,
        (
            "CIGARRILLO", "CIGARRILLOS", "CIGARILLO", "TABACO", "ENCENDEDOR",
            "FOSFORO", "FOSFOROS", "MECHERA", "MALBORO", "MARLBORO",
            "L & M", "LUCKY STRIKE",
        ),
    ):
        return _result("Cigarrillos y encendedores", "keyword:tobacco")

    # Ropa de piscina e interior. No se usa "vestido", "top" ni
    # "pantaloneta" por si solos para evitar clasificar ropa general.
    if _has_any(
        text,
        (
            "FLOTADOR", "FLOTADORES", "FLOT BRASITO", "FLOT LANCHA",
            "FLOT RUEDA", "BRAZITO NATACION", "BRASITO NATACION",
            "MANGUITO NATACION", "TAPA OIDOS CON NARIGUERA",
            "NARIGUERA", "GAFAS NATACION", "GORRO NATACION",
            "INFLABLE PISCINA", "COLCHONETA PISCINA",
        ),
    ):
        return _result("Piscina y acuáticos", "keyword:aquatic")
    if _has_any(
        text,
        (
            "VESTIDO DE BANO", "VESTDO DE BANO", "VESTIDI DE BANO",
            "TRAJE DE BANO", "PANTALONETA DE BANO", "PANTALONETA BANO",
            "SALIDA DE BANO", "BIKINI", "TANKINI", "TRIKINI",
        ),
    ):
        return _result("Trajes de baño", "keyword:swimwear")
    if _has_any(
        text,
        (
            "ROPA INTERIOR", "BRASIER", "BRASSIER", "BRALETTE", "PANTY",
            "PANTYS", "CACHETERO", "BOXER", "CALZON", "CALZONCILLO",
            "TIRA TRANSPARENTE BRA", "TIRA TELA BRA", "CORDON BRA STRAPLESS",
            "SILICON BRA", "MEDIA PANTY",
        ),
    ):
        return _result("Ropa interior", "keyword:underwear")

    # Salud y cuidado. Estas reglas preceden alimentos y hogar para resolver
    # casos como PASTA DENTAL, ACEITE COSMETICO y AGUA OXIGENADA.
    if _has_any(
        text,
        (
            "DOG CHOW", "CAT CHOW", "DOGOURMET", "DOGURMET", "WHISKAS", "PURINA FELIX",
            "FELIX PURINA", "MIRRINGO", "PEDIGREE", "RINGO", "MONELLO",
            "OH MAIGAT", "UNIKAT", "MY MICHIS", "MASCOTIKAS", "NUTRISS GATOS",
            "SUDES PET", "HUESO CARNAZA", "ATUN FELIX", "DONKAT", "OH MAI GAT",
            "FELIX", "ALPISTE", "ARENA CALABAZA", "ARENA KING CAT",
            "CANARIOS Y PERICOS", "GATSY", "MUAU", "DOW CHAOW", "TUFFY",
            "NUTRISS", "DONKAN", "ATUN FELI",
        ),
    ):
        return _result("Mascotas", "brand:pets")
    if _has_any(text, ("TENA", "PANAL ADULTO", "PANALES ADULTO")):
        return _result("Cuidado personal y belleza", "keyword:adult_incontinence")
    if _has_any(text, ("FABULOSO", "AXION", "PLATOX", "LOZACREM")):
        return _result("Limpieza del hogar", "brand:household_cleaning")

    baby_signal = _has_any(
        text,
        (
            "COMPOTA", "TETERO", "BIBERON", "CHUPETE", "CREMA ANTIPANAL",
            "TOALLITAS BEBE", "TOALLAS HUMEDAS BEBE", "FORMULA INFANTIL",
            "LECHE MATERNIZADA", "HUGGIES", "WINNY", "PEQUENIN", "PAMPERS",
            "ARRURRU", "JOHNSON", "JOHNSONS", "INFANTICO", "KIT INFANTIL ASEO",
            "NESTUM",
        ),
    ) or (
        _has_any(text, ("PANAL", "PANALES"))
        and not _has_any(text, ("MIEL", "ABEJA"))
    ) or (
        _has_any(text, ("BEBE", "BABY"))
        and not _has_any(text, ("BABY BEEF",))
    )
    if baby_signal:
        return _result("Bebé", "keyword:baby")
    if _has_any(
        text,
        (
            "TOALLA HIGIENICA", "TOALLAS HIGIENICAS", "PROTECTOR DIARIO",
            "PROTECTORES DIARIOS", "TAMPON", "TAMPONES", "COPA MENSTRUAL",
            "NOSOTRAS", "KOTEX", "CAREFREE", "STAYFREE", "TOALLA NOS",
            "TOALLAS NOST",
        ),
    ):
        return _result("Cuidado femenino", "keyword:feminine_care")
    if _has_any(
        text,
        (
            "CREMA DENTAL", "PASTA DENTAL", "CEPILLO DENTAL", "CEPILLO DE DIENTES",
            "HILO DENTAL", "SEDA DENTAL", "ENJUAGUE BUCAL", "ENJUAGUE ORAL",
            "ORAL B", "ORALB", "COLGATE", "LISTERINE", "PROTESIS DENTAL",
            "CEPILLO PRO",
        ),
    ):
        return _result("Cuidado dental", "keyword:dental_care")
    if _has_any(
        text,
        (
            "SHAMPOO", "CHAMPU", "ACONDICIONADOR", "TRATAMIENTO CAPILAR",
            "MASCARILLA CAPILAR", "TINTE CAPILAR", "TINTE PARA CABELLO",
            "CREMA PARA PEINAR", "GEL PARA CABELLO", "GEL CABELLO", "LACA CABELLO",
            "REPARADOR DE PUNTAS", "SILICONA REGENERADORA CAPILAR",
            "HEAD SHOULDERS", "PANTENE", "SEDAL", "SAVITAL",
            "NUTRIBELA", "EGO", "GEL FIJADOR", "SHAMP", "SHAMPO", "ACOND", "DOVE SH",
            "DOVE AC", "HEAD AND SHOULDERS", "SILICONA CAPILAR", "TONO A TONO",
            "SEDOSO", "ALMA TRATAMIENTO",
        ),
    ):
        return _result("Cuidado capilar", "keyword:hair_care")
    if _has_any(
        text,
        (
            "ACETAMINOFEN", "IBUPROFENO", "NAPROXENO", "DICLOFENACO", "ASPIRINA",
            "DOLEX", "ADVIL", "SINUTAB", "NORAVER", "ALKA SELTZER", "SAL DE FRUTAS",
            "ESPARADRAPO", "CURITA", "VENDA", "GASA", "TAPABOCAS", "TERMOMETRO",
            "PRESERVATIVO", "CONDON", "CONDONES", "SUERO ORAL", "AGUA OXIGENADA",
            "ALCOHOL ANTISEPTICO", "VITAMINA", "MULTIVITAMINICO", "MEDICAMENTO",
            "BUSCAPINA", "GUANTE DE EXAMEN", "GUANTES DE EXAMEN",
            "GUANTE LATEX", "GUANTES LATEX",
            "TODAY", "JERINGA", "MICROPORE", "ALGODON", "ALCOHOL JGB",
            "ALCOHOL MK", "ALCOHOL OSA", "OSA ALCOHOL", "AMOXICILINA", "VICK",
            "CURE BAND", "CALMIDOL", "PAX DIA", "PAX NOCHE", "NOXPIRIN",
            "MIELTERTOS", "LUMBAL", "IBUFLASH", "SEVEDOL", "OMEPRAZOL",
            "X RAY DOL", "PROPOLVIK", "APRONAX", "NEXT GEL GRIPA",
        ),
    ):
        return _result("Salud y farmacia", "keyword:health")
    if _has_any(
        text,
        (
            "DESODORANTE", "JABON DE TOCADOR", "JABON TOCADOR", "JABON CORPORAL",
            "GEL DE BANO", "CREMA CORPORAL", "CREMA DE MANOS", "LOCION CORPORAL",
            "PERFUME", "COLONIA", "TALCO", "MAQUILLAJE", "POLVO COMPACTO",
            "LABIAL", "PESTANINA", "DELINEADOR", "ESMALTE UNAS", "QUITAESMALTE",
            "PROTECTOR SOLAR", "BLOQUEADOR", "AFEITADORA", "CUCHILLA DE AFEITAR",
            "CREMA DE AFEITAR", "ACEITE COSMETICO", "ACEITE CORPORAL", "AGUA MICELAR",
            "CREMA FACIAL", "MASCARILLA FACIAL", "YODORA",
            "REPELENTE", "AUTAN", "SCHICK", "GILLETTE", "VASELINA",
            "PINZA CABELLO", "PINZAS CABELLO", "PINZA PARA CABELLO",
            "DOVE", "REXONA", "PROTEX", "NEKO", "PALMOLIVE", "PRESTOBARBA",
            "PRESTO BARBA", "MINORA", "VENUS SIMPLY", "PONDS", "LUBRIDERM",
            "LADY SPEED", "SPEED STICK", "BALANCE CLINICAL", "BALANCE CREMA",
            "TOALLITAS HUMD DESMAQUILLADORAS", "TOALLITAS HUMEDAS DESMAQUILLADORAS",
            "ANTIBACTERIAL", "DEPILADOR", "ENCRESPADOR", "PERFILADOR", "CORTA UNAS",
            "BALACA", "SCRONCHIS", "PINZA PERLA", "PINZA BRILLANTE",
            "PINZAS SURTIDAS", "PINZAS TONO", "PINZAS CORBATIN", "CAIMAN PEQUENO",
            "CAIMAN MEDIANO", "CAIMAN GIRASOL", "CAIMAN FLORES", "MONO COLORES",
            "MONO MEDIANO", "MONO PUNTOS", "ARETE", "ARETES", "PULSERA",
            "PULSERAS", "PEINILLA", "CAUCHITOS", "BANDAS SURTIDAS", "VOGUE",
            "GILLETTE GEL",
        ),
    ):
        return _result("Cuidado personal y belleza", "keyword:personal_care")

    # Aseo y mascotas antes que alimentos: una fragancia de manzana, un jabon
    # de coco o comida de pollo para perro no son productos comestibles humanos.
    if _has_any(
        text,
        (
            "AMBIENTADOR", "AROMATIZANTE", "INSECTICIDA", "RATICIDA", "MATAMOSCAS",
            "ESPIRAL MOSQUITO", "PASTILLA MOSQUITO", "GLADE", "BONAIRE",
            "BON AIRE", "RAID", "BAYGON", "ELIMINADOR OLOR", "AROMAX", "VENENO",
        ),
    ):
        return _result("Ambientadores y control de plagas", "keyword:air_and_pest")
    if _has_any(
        text,
        (
            "DETERGENTE", "SUAVIZANTE", "QUITAMANCHAS", "BLANQUEADOR DE ROPA",
            "JABON REY", "JABON PARA ROPA", "JABON DE ROPA", "PRELAVADO",
            "ALMIDON PARA ROPA", "DERSA", "ARIEL", "FABULOSO DETERGENTE",
            "SUAVITEL", "FAB", "FAV", "VANISH", "AROMATEL", "DETERG",
            "COCO BARRA PRENDA DELICADA", "JABON COCO EL ORIGINAL",
            "JABON LIQUIDO TIPO REY", "REY JABON BARRA LIQUIDO",
            "JABON PURO CON BICARBONATO", "JABON TOP COMBI",
            "VEL ROSITA JABON", "JABON TOP TERRA", "PURO JABON FUERZA",
        ),
    ):
        return _result("Lavandería", "keyword:laundry")
    if _has_any(
        text,
        (
            "LAVALOZA", "LAVA LOZA", "LIMPIADOR", "LIMPIAVIDRIOS", "LIMPIA VIDRIOS",
            "DESINFECTANTE", "MULTIUSOS", "CLORO", "HIPOCLORITO", "BLANQUEADOR",
            "ESPONJA", "BRILLA OLLAS", "TRAPERO", "ESCOBA", "RECOGEDOR",
            "CEPILLO PISO", "CEPILLO PLANCHA", "GUANTE ASEO", "BOLSA DE BASURA",
            "LIMPION", "LIPION", "BALLETILLA", "BETUN", "LUSTRAMUEBLES",
            "JABON LOZA", "JABON LAVALOZA",
            "BLANCOX", "VARSOL", "LIMPIDO", "GUANTE DOMESTICO", "GUANTES DOMESTICOS",
            "TERGO GLOP",
            "BOLSA BASURA", "BOLSAS DE BASURA", "BOLSA PARA LA BASURA", "BOLSA NEGRA",
            "PANO ABSORBENTE", "TOP ESPONJILLA", "BRIO", "FROTEX", "MR MUSCULO",
            "PATO TANQUE", "TOALLA MICROFIBRA", "CEPILLO MULTI HOGAR",
            "CEPILLO DE MANO", "CEPILLO SANITARIO", "GUANTES EL REY", "GUANTE FUROR",
            "BOMBA SUCCION", "BOMBA DE SUCCION", "CHUPAS SANITARIA",
            "ACIDO MURIATICO", "ACIDO MUIRIATICO", "CLOROX", "CREOLINA",
            "SCOTCH BRITE", "VASRSOL", "CERA ESCARLATA", "CERA AMARILLA",
            "AJAX", "BOMBA DE LIMPIEZA",
        ),
    ):
        return _result("Limpieza del hogar", "keyword:household_cleaning")
    if _has_any(
        text,
        (
            "COMIDA PARA PERRO", "COMIDA PERRO", "COMIDA PARA GATO", "COMIDA GATO",
            "ALIMENTO PARA PERRO", "ALIMENTO PARA GATO", "ALIMENTO MASCOTA",
            "CONCENTRADO PERRO", "CONCENTRADO GATO", "ARENA PARA GATO", "ARENA GATO",
            "PEDIGREE", "WHISKAS", "DOG CHOW", "CAT CHOW", "MIRRINGO", "CHUNKY",
            "HUESO CANINO", "SUDES PET", "MASCOTA", "MASCOTAS",
        ),
    ):
        return _result("Mascotas", "keyword:pets")
    # "JABON" por sí solo se resuelve después de lavandería, loza y mascotas;
    # así JABON REY y JABON LAVALOZA conservan su categoría específica.
    if _has_any(text, ("JABON",)):
        return _result("Cuidado personal y belleza", "keyword:generic_soap", "medium")

    # Articulos no alimentarios.
    if _has_any(
        text,
        (
            "CUADERNO", "LAPIZ", "LAPICERO", "BOLIGRAFO", "BORRADOR", "TAJALAPIZ", "SACAPUNTA",
            "MARCADOR", "CARTULINA", "BLOCK", "REGLA ESCOLAR", "CORRECTOR",
            "SILICONA LIQUIDA", "COLBON", "PEGANTE ESCOLAR", "CINTA TRANSPARENTE",
            "LETRA DE CAMBIO", "SOBRE MANILA", "CARPETA", "TEMPERA", "PLASTILINA",
            "DORICOLOR", "FABER CASTELL", "RESALTADOR", "PAPEL CREPE",
            "PAPEL DE REGALO", "PAPEL KRAFF", "PAPEL SEDA", "CINTA PAPEL",
            "CINTA SIPEGA", "CINTA TESA", "CINTA EMPAQUE", "PINCEL",
            "SOBRE BLANCO", "SOBRE DE MANILA", "TIJERA", "TIJERAS", "FOMI",
            "SHARPIE", "CARTON PAJA", "GANCHO LEGAJADOR", "HOJA DE VIDA",
            "HOJA DE EXAMEN", "LEGAJADOR", "CARTEL SE ARRIENDA", "CARTEL SE VENDE",
            "TEMPERAS", "TRANSPORTADOR", "CONTRATO ARRENDAMIENTO",
            "CONTRATO DE ARRENDAMIENTO", "PAPEL PERIODICO", "PAPEL FOTOGRAFICO",
            "PAPEL CONTAC", "SI PEGA", "SIPEGA", "PEGANTE BARRA", "BISTURI",
            "CINTA ENMASCARAR", "OFFI ESCO", "PAPER MATE", "RICIBO DE CAJA",
        ),
    ):
        return _result("Papelería y escolar", "keyword:stationery")
    if _has_any(
        text,
        (
            "BOMBILLO", "LINTERNA", "VARTA", "DURACELL", "CABLE", "CARGADOR CELULAR", "CARGADOR USB", "AUDIFONO",
            "REGLETA ELECTRICA", "EXTENSION ELECTRICA", "PILA", "PILAS", "BATERIA",
            "TORNILLO", "ABRAZADERA", "CANDADO", "DESTORNILLADOR", "ALICATE",
            "MARTILLO", "CINTA AISLANTE", "PISTOLA DE SILICONA", "PEGANTE PVC",
            "ACCESORIOS CELULARES", "ADAPTADOR ELECTRICO",
            "CINTA TEFLON", "DURACEL", "ENERGIZER", "NIPPON BINBILLO",
            "CINTA ELECTRICA", "PEGANTE INSTANTANEO", "GREEN POWE PEGAMENTO",
            "ASTRO GLUE", "SUPER GLUE", "SILICONA DELGADA", "SILICONA GRUESA",
        ),
    ):
        return _result("Ferretería, eléctrico y tecnología", "keyword:hardware_tech")
    if _has_any(
        text,
        (
            "PAPEL HIGIENICO", "SERVILLETA", "SERVILLETAS", "TOALLA DE COCINA", "TOALLA COCINA",
            "PAPEL ALUMINIO", "ALUMINIO", "VINIPEL", "VASO DESECHABLE",
            "PLATO DESECHABLE", "CUBIERTO DESECHABLE", "DESECHABLE", "PITILLO",
            "BOLSA ZIPLOC", "BOLSA HERMETICA", "FILTRO DE CAFE",
            "DARNEL", "VASOS DE PAPEL", "CONTENEDOR",
            "PALO PINCHO", "PALOS PINCHO", "PALITOS PALETA", "PALITO PALETA",
            "PALOS PARA HELADO", "PALO PARA HELADO",
            "ALUMIFLEX", "BOLSA GRANDE", "PAQUETE DE BOLSA", "BOLSAS CIERRE FACIL",
            "PALILLOS", "VASO 7 ONZAS", "VASO 7 OZ", "VASO 9 OZ", "VASO 10 ONZ",
            "VASO NEGRO 5 ONZAS", "PANUELOS", "KLEENEX", "SCOTT", "ROSAL",
            "PAPEL HIGIEN", "TOALLA FAMILA", "TOALLA FAMILIA", "TOALLAS DE COCINCA",
            "COPA AGUARDIENTERA", "COPA 1 0 ONZA", "POSTOBON VASO",
            "ACOLCHAMAX", "PRACTIDIARIA", "PRACTIDIARIAS", "SERVLLETA",
            "SEERVILLETA", "MEGAROLLO", "MEGARROLLO", "FAMILIA EXPERT",
            "FAMILIA GREEN", "FAMILIA FAMILIAR LIMPIEZA CONFIABLE",
        ),
    ):
        return _result("Papel, desechables y cocina", "keyword:paper_disposables")
    if _has_any(
        text,
        (
            "SERVILLETERO", "VELA DECORATIVA", "VELA NAVIDENA", "MANTEL", "TAZA",
            "VASO VIDRIO", "COPA VIDRIO", "CUCHARA", "TENEDOR", "CUCHILLO COCINA",
            "OLLA", "SARTEN", "RECIPIENTE", "PORTACOMIDA", "GANCHO DE ROPA",
            "CAUCHO DE GOMA", "CAUCHO POWER", "CAUCHO SILICONADO",
            "VELA", "VELON", "VELADORA", "VELAS", "BOMBAS CUMPLEANOS",
            "BOMBAS R12", "ALCANCIA", "ALCANCIAS", "CAUCHO PARA PITADORA",
            "CAUCHO PITADORA", "GANCHO ROPA", "ORGANIZADOR PORTAESCOBA",
        ),
    ):
        return _result("Hogar y accesorios", "keyword:home_accessories")

    # Casero y Bocatto son líneas de helado. Se resuelven antes de bebidas y
    # salsas para que sabores como ron-pasas o caramelo salado no ganen por el
    # ingrediente, sin afectar productos BIMBO CASERO.
    if name_text.startswith("CASERO ") or name_text.startswith("BOCATTO ") or _has_any(name_text, ("CONO BOCATTO",)):
        return _result("Helados y congelados", "brand:ice_cream")
    if name_text == "BARRILETE":
        return _result("Dulces y chocolates", "exact:candy_brand")
    if _has_any(text, ("NUCITA NUGGETS",)):
        return _result("Dulces y chocolates", "exception:candy_nuggets")
    # La gelatina es una mezcla para preparar incluso cuando la marca o el
    # sabor coinciden con una bebida (p. ej. TUTTI FRUTTI o ALQUERÍA).
    if _has_any(text, ("GELATINA",)) and not _has_any(
        text, ("GOMITA", "GOMITAS", "DULCE", "CARAMELO")
    ):
        return _result("Cereales, harinas y mezclas", "keyword:gelatin")

    # Bebidas. Salud se evaluo antes para que AGUA OXIGENADA no sea agua.
    if _has_any(
        text,
        (
            "AGUARDIENTE", "CERVEZA", "WHISKY", "WHISKEY", "VINO", "RON", "VODKA",
            "TEQUILA", "GINEBRA", "BRANDY", "CHAMPAGNE", "LICOR", "APERITIVO",
            "CREMA DE WHISKY", "BUCHANAN", "BUCANANS", "CHIVAS REGAL",
            "CORONA EXTRA",
            "CLUB COLOMBIA", "COSTENA BACANA", "COSTENA BANACA", "COSTENA GRIS",
            "COLA POLA", "COLA Y POLA", "POKER", "FOUR LOKO", "MICHELOB",
            "REDDS", "REDD S", "STELLA ARTOIS", "AGUILA LIGHT", "AGUILA ORIGINAL",
            "CERVEZA AGUILA", "CERV AGUILA", "ANDINA", "SMIRNOFF",
            "CORONITA EXTRA", "WSK PASSPORT",
        ),
    ) and not _has_any(text, ("GALLETA", "WAFER", "PONQUE", "TORTA")):
        return _result("Cervezas, vinos y licores", "keyword:alcohol")
    if _has_any(text, ("BEBIDA EN POLVO", "REFRESCO EN POLVO", "TANG", "FRUTINO", "BOKA", "CLIGHT", "SUNTEA", "POSTOBON PANELADA")):
        return _result("Bebidas en polvo", "keyword:powdered_drinks")
    if _has_any(
        text,
        (
            "GASEOSA", "COCA COLA", "PEPSI", "SPRITE", "7UP", "KOLA", "COLA ROMAN",
            "SODA", "MALTA", "MALTIN", "ENERGIZANTE", "RED BULL", "SPEED MAX",
            "MONSTER ENERGY", "MONSTER", "GATORADE", "POWERADE", "ELECTROLIT",
            "PEDIALYTE", "SUEROX", "VIVE 100", "VIVE100", "H2O", "COCACOLA", "COLOMBIANA",
            "AMPER", "HIDRALYTE", "QUATRO", "FANTA", "SCHWEPPES",
            "POSTOBON MANZANA", "MANZANA POSTOBON", "POSTOBON UVA", "UVA POSTOBON",
            "POSTOBON BRETANA", "POSTOBON GINGER", "POSTOBON H2 OH",
            "POSTOBON SEVEN UP", "POSTOBON TROPIKOLA", "POSTOBON NARANJA",
            "POSTOBON CANADA DRY", "POSTOBON TORONJA", "POSTOBON GATORLIT",
        ),
    ) and not _has_any(text, ("SODA CAUSTICA", "BICARBONATO DE SODA", "POSTOBON HIT", "ACQUA")):
        return _result("Gaseosas, maltas y energizantes", "keyword:soft_drinks")
    if _has_any(text, ("JUGO", "NECTAR", "BEBIDA DE FRUTA", "BEBIDA CON FRUTA", "DEL VALLE", "JUGO HIT", "HIT", "PULP", "TAMPICO", "TANGELO", "FRUTTO", "RICAROMA", "COOL DRINK", "FUZETEA", "SAVILOE", "FRUTAL", "LIKE", "TUTTI FRUTTI")):
        return _result("Jugos y bebidas de fruta", "keyword:fruit_drinks")
    if _has_any(
        text,
        (
            "AGUA MINERAL", "AGUA PURIFICADA", "AGUA POTABLE", "AGUA SABORIZADA",
            "AGUA CON GAS", "AGUA SIN GAS", "BOTELLA DE AGUA", "AGUA BRISA",
            "AGUA CRISTAL", "AGUA CIELO", "AGUA MANANTIAL", "AGUA CON COLAGENO",
            "POSTOBON ACQUA", "POSTOBON CRISTAL", "BRISA", "MONTESION", "GLACIAL AGUA",
        ),
    ):
        return _result("Aguas", "keyword:water")
    # Alimentos preparados y conservas preceden sus ingredientes (p. ej.
    # cazuela de mariscos, salsa de tomate y frutas en almibar).
    if _has_any(
        text,
        (
            "PIZZA", "LASAGNA", "HAMBURGUESA", "NUGGET", "NUGGETS", "EMPANADA", "TAMAL",
            "CAZUELA", "PAPA PREFRITA", "PAPAS PREFRITAS", "PASTEL DE POLLO",
            "COMIDA PREPARADA", "PLATO PREPARADO", "PRECOCIDO", "PRECOCIDA",
            "CONGELADO", "CONGELADA", "DEDITOS DE QUESO",
            "CALYPSO PAPA", "CALYPSO YUCA", "CALYPSO MIX",
            "CALIPSO PAPA", "CALIPSO VERDURA", "PAPA A LA FRANCESA",
            "PAPA FRANCESA", "RICONGELISTO", "MIXTURA DE MARISCOS", "PAELLA",
            "CALYPSO CROCRETA", "CALYPSO CHULETA",
        ),
    ):
        return _result("Comidas preparadas y congeladas", "keyword:prepared_frozen")
    if _has_any(
        text,
        (
            "ENLATADO", "ENLATADA", "CONSERVA", "CONSERVAS", "COSERVA", "COSERVAS",
            "ENCURTIDO", "ACEITUNA", "ACEITUNAS", "ALCAPARRA",
            "ATUN", "SARDINA", "FRUTAS EN ALMIBAR", "FRUTA EN ALMIBAR",
            "BREVAS EN ALMIBAR", "DURAZNOS EN ALMIBAR", "PALMITOS",
            "ARVEJAS NATURALES", "GARBANZOS AL NATURAL", "ENSALADA DE VEGETALES",
            "CHAMPINONES TAJADOS",
            "ZENU ARVEJAS", "ZENU ENSALADA", "ZENU FRIJOLES", "ZENU CHAMPINON",
            "SAN JORGE MAIZ", "SAN JORGE ENSALADA", "SAN JORGE LENTEJAS",
            "SAN JORGE ARVEJA", "VAN CAMPS AGUA", "VAN CAMPS ATUN",
            "VAN CAMPS ENSALADA", "VAN CAMPS SARDINA", "VAN CAMPS LOMITO",
            "VAN CAMPS LOMOS", "ATUM VAN CAMPS", "VIKINGOS EN AGUA", "FRUTAS ERN ALMIBAR",
            "CEREZAS MARRASQUINO",
        ),
    ):
        return _result("Enlatados y conservas", "keyword:canned_preserved")
    # El sabor no cambia la naturaleza de un pasabocas. Esta excepción evita
    # que MANÍ CON PIMIENTA o PAPAS PAPRIKA entren como condimentos.
    if (
        _has_any(text, ("MANI", "PAPAS", "MANICERO"))
        and not _has_any(text, ("MANTEQUILLA DE MANI", "MANTEQUILLA MANI"))
    ):
        return _result("Snacks y pasabocas", "exception:seasoned_snack")
    if _has_any(
        text,
        (
            "SALSA", "KETCHUP", "MOSTAZA", "MAYONESA", "VINAGRE", "CONDIMENTO",
            "SAZONADOR", "ESPECIA", "CALDO", "SOPA", "CREMA INSTANTANEA",
            "PASTA DE TOMATE", "ADEREZO", "CURCUMA", "PIMIENTA", "COMINO",
            "SAZONAREY", "COLORREY", "BICARBONATO", "CANELA", "CLAVO", "TAJIN",
            "MAGGI", "LA SOPERA", "EL REY", "LEVAPAN ESENCIA", "PAPRIKA PURA",
            "PAPRIKA A LA MESA", "PAPRIKA EL REY",
            "RICOSTILLA", "A LA MESA", "SAN ADOBO", "BARY VINAGRETA",
        ),
    ):
        return _result("Salsas, condimentos y sopas", "keyword:sauces_seasoning")
    if _has_any(text, ("GALLETA", "GALLETAS", "WAFER", "WAFERS", "TOSTADA", "TOSTADAS", "TOSTADO", "TOSTADOS", "CLUB SOCIAL", "COOKIES", "OREO", "FESTIVAL", "BARQUILLO", "PIAZZA JIRAFA", "NOEL", "DUX", "CHOKIS", "SALTIN", "DUCALES", "COCOSETTE", "MAMUT")):
        return _result("Galletas y tostadas", "keyword:biscuits_toast")
    if _has_any(
        text,
        (
            "PAPAS", "PAPA FRITA", "PASABOCA", "MANI", "CHICHARRON", "DORITOS",
            "CHEETOS", "PLATANITOS", "CRISPETAS", "PALOMITAS", "SNACK", "KRAKS",
            "DE TODITO", "YUPI", "TOSTI", "NATUCHIPS", "PRINGLES", "POPETAS",
            "MAIZITOS", "TOSH BARRA", "TOSH", "MARGARITA", "DETODITO", "NUTHOS",
            "GOLPE", "CHOCLITOS", "TOSTON", "TROCIPOLLO", "TAKIS", "CHEESE TRIS",
            "YUPIS", "PATTY PASABOCAS", "YUQUILLAS", "WACKY", "TRUENO CANGREJITO",
            "TRUENO PULPITO", "BANAS", "PACHAS", "FOSFORITOS",
            "SUPER RICA TROCITOS", "SUPER RICAS TROCITOS",
        ),
    ):
        return _result("Snacks y pasabocas", "keyword:snacks")
    if _has_any(
        text,
        (
            "CHOCOLATINA", "BOMBON", "BONBONBUM", "CARAMELO", "CHUPETA", "GOMITA",
            "GOMITAS", "CHICLE", "TRIDENT", "MENTA", "DULCE", "CONFITE",
            "MARSHMALLOW", "MASMELO", "HUEVO SORPRESA", "GOMITA HUEVO", "HALLS",
            "PIN POP", "PINPOP",
            "JUMBO FLOW", "JUMBO MINI", "JUMBO MIX", "JUMBO BROWNIE", "JUMBO MIMOS",
            "MILLOWS", "OKA LOCA", "OKA LOKA", "SUPERCOCO", "BIANCHI",
            "FINI", "FINIROLLER", "CANDYRANCH", "FRUNAS", "FERRERO", "TIPITIN",
            "BUBBALOO", "PIRULITO", "TROLLI", "TROLLY", "SWEET GOMYS",
            "BARRILETE CHOCO", "BON BON BUM", "CHOCOBREAK", "CHOCO LYNE", "CHOCO BALL",
            "MONTBLANC", "COFFE DELIGHT",
        ),
    ):
        return _result("Dulces y chocolates", "keyword:candy")
    if _has_any(
        text,
        (
            "RAMEN", "NOODLES", "NCODLES", "NUDOS", "NISSIN",
            "SPAGHETTI", "ESPAGUETI",
        ),
    ):
        return _result("Arroz, granos y pastas", "keyword:instant_noodles")
    if _has_any(
        text,
        (
            "CARNE", "POLLO", "CERDO", "RES", "PESCADO", "MARISCO", "CAMARON",
            "SALCHICHA", "SALCHICHON", "CHORIZO", "MORTADELA", "JAMON", "JAMONETA", "TOCINETA", "TOCINO",
            "EMBUTIDO", "VISCERA", "VISERAS", "COSTILLA", "LOMO", "PECHUGA", "TILAPIA", "TRUCHA", "SALMON",
            "PIERNA", "CONTRA MUSLO", "MENUDENCIA", "MENUDENCIAS", "MORTADEL",
            "MORCILLA", "CHICHARRONCITOS", "HUESITO CARNUDO",
        ),
    ):
        return _result("Carnes, pescados y embutidos", "keyword:meat_fish")
    if _has_any(text, ("HUEVO", "HUEVOS", "CUBETA HUEVO", "PANAL HUEVO")):
        return _result("Huevos", "keyword:eggs")
    if _has_any(
        text,
        (
            "HELADO", "CREMA HELADA", "PALETA", "PALETA DE HELADO", "CONO", "CONO DE HELADO",
            "POSTRE CONGELADO", "BONICE", "POLET", "VASO HELADINO",
            "VASO SUNDAE",
        ),
    ):
        return _result("Helados y congelados", "keyword:ice_cream")
    if _has_any(
        text,
        (
            "AREQUIPE", "MARGARINA", "MERMELADA", "CREMA DE AVELLANA",
            "MANTEQUILLA DE MANI", "MANTEQUILLA MANI", "NUTELLA", "UNTABLE", "CAMPI",
        ),
    ) and not _has_any(
        text,
        (
            "BIMBO", "RAMO", "BROWNIE", "PONQUE", "TORTA", "GALLETA",
            "GALLETAS", "BATILADO", "MAIZENA", "HARINA", "CEREAL", "PAN", "AREPA",
        ),
    ):
        return _result("Untables y mermeladas", "keyword:spreads_before_dairy")
    if _has_any(text, ("GELATINA",)):
        return _result("Cereales, harinas y mezclas", "keyword:gelatin_before_dairy")
    if _has_any(name_text, ("VIVA SOYA",)) and not _has_any(name_text, ("ACEITE",)):
        return _result("Lácteos y refrigerados", "brand:plant_drink")
    if _has_any(
        text,
        (
            "LECHE", "QUESO", "QUESILLO", "CUAJADA", "YOGUR", "YOGURT", "YOGOYOGO",
            "BONYURT", "ALPINITO", "ALPINETTE", "YOX", "KUMIS",
            "YOGO PREMIO", "REGENERIS", "CREMOSINO", "KEFIR", "FEKIR",
            "CREMA DE LECHE", "SUERO COSTENO", "MANTEQUILLA", "LECHE EN POLVO",
            "BOGGY", "QUESITO", "PARMESANO", "KLIM", "LA LECHERA",
            "LECHERITA", "ALPINA", "ALQUERIA", "COLANTA", "BATILADO",
            "BON YURT", "CELEMA BEBIDA DE ALMENDRA", "ALMONDEE BEBIDA DE ALMENDRA",
        ),
    ) and not _has_any(text, ("AREPA", "PAN", "PONQUE", "TORTA", "GALLETA", "BARRA")):
        return _result("Lácteos y refrigerados", "keyword:dairy")
    if _has_any(
        text,
        (
            "PAN", "AREPA", "TORTA", "PONQUE", "CROISSANT", "CROASAN", "BUNUELO",
            "ALMOJABANA", "BROWNIE", "MUFFIN", "REPOSTERIA", "PANDEBONO",
            "BIMBO", "RAMO", "GALA", "CHOCORRAMO", "BIZCOCHO", "BIZCOCHOS", "CALENTANOS",
            "AREPAS", "GUADALUPE", "PASTEL", "MOGOLLA",
        ),
    ):
        return _result("Panadería, arepas y repostería", "keyword:bakery")
    if _has_any(
        text,
        (
            "ARROZ", "LENTEJA", "GARBANZO", "FRIJOL SECO", "FRIJOL CARGAMANTO",
            "ARVEJA SECA", "PASTA ALIMENTICIA", "SPAGHETTI", "ESPAGUETI", "MACARRON",
            "FIDEO", "LASO PASTA", "CONCHA PASTA", "DORIA", "ARVEJA AMARILLA",
            "ARVEJA VERDE", "DIANA GARBANZOS", "DIANA FRIJOL", "DIANA FRIJOLES",
            "DIANA MAIZ PIRA", "MAIZ PIRA DIANA", "RIO GRANDE FRIJOL", "PASTA COMARRICO",
            "MAIZ PIRA",
        ),
    ):
        return _result("Arroz, granos y pastas", "keyword:rice_grains_pasta")
    if _has_any(
        text,
        (
            "CEREAL", "AVENA", "GRANOLA", "HARINA", "MAIZENA", "FECULA",
            "MEZCLA PARA PANCAKE", "MEZCLA PARA TORTA", "ZUCARITAS", "CORN FLAKES",
            "GELATINA", "ANILLOS", "SEMILLAS DE CHIA", "LINAZA", "AJONJOLI",
            "PROMASA", "RICAVENA", "NATRI QUINUA", "UVA PASA", "UVAS PASAS",
            "CIRUELAS PASAS", "COCO RALLADO", "LEVADURA",
        ),
    ):
        return _result("Cereales, harinas y mezclas", "keyword:cereals_flour")
    if _has_any(
        text,
        (
            "ACEITE", "ACEITE VEGETAL", "ACEITE DE OLIVA", "ACEITE GIRASOL", "ACEITE DE SOYA",
            "ACEITE CANOLA", "ACEITE DE COCO", "ACEITE PALMA", "AZUCAR", "SAL",
            "PANELA", "ENDULZANTE", "STEVIA", "MIEL", "REFISAL", "INCAUCA LIFE",
            "PANELADA", "PANELITA",
        ),
    ):
        return _result("Aceites, azúcar, sal y panela", "keyword:oil_sugar_salt")
    if _has_any(
        text,
        (
            "MERMELADA", "MARGARINA", "CREMA DE AVELLANA", "MANTEQUILLA DE MANI",
            "MANTEQUILLA MANI", "NUTELLA", "UNTABLE", "AREQUIPE", "CAMPI PAISA",
            "LA FINA", "RAMA COCINA", "RAMA PARA COCINAR", "GUSTOSITA", "AUNT JEMIMA",
        ),
    ):
        return _result("Untables y mermeladas", "keyword:spreads")
    if _has_any(
        text,
        (
            "CAFE", "CHOCOLATE DE MESA", "CHOCOLATE PARA MESA", "CACAO", "COCOA",
            "AROMATICA", "INFUSION", "TEA", "TE VERDE", "TE NEGRO", "TE ROJO",
            "TE FRIO", "TE HINDU", "HINDU TE", "HATSU", "CIDRON", "COLCAFE", "NESCAFE", "MILO",
            "TISANAS", "JUAN VALDES", "SELLO ROJO", "TOSTAO", "LUKAFE",
        ),
    ):
        return _result("Café, chocolate e infusiones", "keyword:coffee_infusion")
    if _has_any(
        text,
        (
            "CHOCOLATINA", "BOMBON", "BONBONBUM", "CARAMELO", "CHUPETA", "GOMITA",
            "GOMITAS", "CHICLE", "TRIDENT", "MENTA", "DULCE", "CONFITE",
            "MARSHMALLOW", "MASMELO", "CHOCOLATE", "TRULULU", "GRISSLY", "JET", "HALLS", "NUCITA", "KINDER",
        ),
    ):
        return _result("Dulces y chocolates", "keyword:candy_chocolate")

    # Fruver se deja al final para que los sabores (galleta de fresa, shampoo
    # de manzana, salsa de tomate) hereden la categoria del producto y no la de
    # la fruta mencionada.
    produce_terms = (
        "AGUACATE", "BANANO", "KIWI", "COCO", "FRESA", "MORA", "GRANADILLA", "GUAYABA",
        "LIMON", "LULO", "MANDARINA", "MANGO", "MANZANA", "MARACUYA", "MELON",
        "NARANJA", "PAPAYA", "PERA", "PERAS", "PINA", "PITAYA", "SANDIA", "UVA",
        "AHUYAMA", "APIO", "ARVEJA", "BATAVIA", "BROCOLI", "CEBOLLA", "REPOLLO",
        "CHAMPINON", "CIDRA", "LECHUGA", "ESPINACA", "HABICHUELA", "MAZORCA",
        "PEPINO", "PIMENTON", "REMOLACHA", "ZUKINI", "TOMATE", "ZANAHORIA",
        "ARRACACHA", "COLICERO", "PAPA CRIOLLA", "PAPA NEGRA", "PLATANO", "YUCA",
        "AJO", "CILANTRO", "GUASCA", "JENGIBRE", "PEREJIL", "SABILA", "TOMILLO",
        "LAUREL", "FRIJOL CASCARA", "FRIJOL DESGRANADO", "FRIJOL VERDE", "ARANDANOS",
        "GUANABANA", "FRUVER",
    )
    produce_presentation = _has_any(
        name_text,
        (
            "XKG", "X KG", "XGR", "X GR", "XUND", "X UND", "XUNI", "X UNI",
            "BANDEJA", "PAQUETE",
        ),
    )
    if (
        _has_any(text, produce_terms)
        and (name_text.startswith("FR ") or produce_presentation or _has_any(text, ("FRUVER",)))
    ):
        return _result("Frutas y verduras", "keyword:produce", "medium")

    return _result("Sin categoría", "fallback:no_reliable_match", "low")
