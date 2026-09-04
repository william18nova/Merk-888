from collections import Counter

from django.test import SimpleTestCase

from mainApp.services import product_categorization as categorization
from mainApp.services.product_categorization import (
    CATEGORY_DEFINITIONS,
    MANUAL_REVIEW,
    Classification,
    classify_product,
    normalize_product_text,
)


EXPECTED_CATEGORIES = (
    "Frutas y verduras",
    "Carnes, pescados y embutidos",
    "Comidas preparadas y congeladas",
    "Huevos",
    "Lácteos y refrigerados",
    "Panadería, arepas y repostería",
    "Arroz, granos y pastas",
    "Cereales, harinas y mezclas",
    "Aceites, azúcar, sal y panela",
    "Untables y mermeladas",
    "Salsas, condimentos y sopas",
    "Enlatados y conservas",
    "Snacks y pasabocas",
    "Galletas y tostadas",
    "Dulces y chocolates",
    "Helados y congelados",
    "Café, chocolate e infusiones",
    "Bebidas en polvo",
    "Aguas",
    "Gaseosas, maltas y energizantes",
    "Jugos y bebidas de fruta",
    "Cervezas, vinos y licores",
    "Limpieza del hogar",
    "Lavandería",
    "Ambientadores y control de plagas",
    "Cuidado capilar",
    "Cuidado dental",
    "Cuidado femenino",
    "Cuidado personal y belleza",
    "Bebé",
    "Salud y farmacia",
    "Mascotas",
    "Papel, desechables y cocina",
    "Papelería y escolar",
    "Ferretería, eléctrico y tecnología",
    "Hogar y accesorios",
    "Ropa interior",
    "Trajes de baño",
    "Piscina y acuáticos",
    "Cigarrillos y encendedores",
    "Recargas y servicios",
    "S.O.S Colombia",
    "Sin categoría",
)


class ProductCategorizationTests(SimpleTestCase):
    def assert_category(self, expected, name, **kwargs):
        result = classify_product(kwargs.pop("product_id", 999999999), name, **kwargs)
        self.assertEqual(result.category, expected, result)
        self.assertIn(result.confidence, {"high", "medium"})
        return result

    def test_canonical_taxonomy_is_exact_and_unique(self):
        names = tuple(definition.name for definition in CATEGORY_DEFINITIONS)
        self.assertEqual(names, EXPECTED_CATEGORIES)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(definition.description for definition in CATEGORY_DEFINITIONS))

    def test_normalization_is_nfkd_case_and_separator_insensitive(self):
        self.assertEqual(normalize_product_text("  Café—Águila / x250 g  "), "CAFE AGUILA X250 G")
        self.assertEqual(normalize_product_text(None), "")

    def test_all_protected_overrides_have_the_expected_distribution(self):
        all_ids = (
            categorization._AQUATIC_PRODUCT_IDS
            | categorization._SWIMWEAR_PRODUCT_IDS
            | categorization._UNDERWEAR_PRODUCT_IDS
        )
        counts = Counter(classify_product(product_id, "NOMBRE AMBIGUO").category for product_id in all_ids)
        self.assertEqual(
            counts,
            {
                "Piscina y acuáticos": 7,
                "Trajes de baño": 54,
                "Ropa interior": 27,
            },
        )
        self.assertEqual(len(all_ids), 88)

    def test_real_protected_products_are_overridden_even_with_ambiguous_names(self):
        cases = (
            (2478, "TAPA OIDOS CON NARIGUERA", "Piscina y acuáticos"),
            (5087, "EVANDRA VESTIDO DE BAÑO TALLA 34", "Trajes de baño"),
            (8567, "SALIDA DE BAÑO TIPO PANTALONETA", "Trajes de baño"),
            (8563, "CONJUNTO NIÑA", "Ropa interior"),
            (5333, "TIRA TELA BRASSIER", "Ropa interior"),
            (5486, "SILICON BRASSIER T 32", "Ropa interior"),
        )
        for product_id, name, expected in cases:
            with self.subTest(product_id=product_id):
                result = classify_product(product_id, name)
                self.assertEqual(result.category, expected)
                self.assertTrue(result.rule.startswith("manual_override:"))

    def test_plaza_destination_and_only_trusted_legacy_categories(self):
        result = classify_product("2978", "NOMBRE ERRADO", plaza_destination_ids={2978})
        self.assertEqual(result.category, "Frutas y verduras")
        self.assertEqual(result.rule, "trusted_source:plaza_destination")

        self.assert_category("Frutas y verduras", "PRODUCTO SIN PISTAS", current_category_name="FRUVER")
        self.assert_category("S.O.S Colombia", "PRODUCTO SIN PISTAS", current_category_name="S.O.S Colombia")

        result = classify_product(1, "PRODUCTO SIN PISTAS", current_category_name="Galletas")
        self.assertEqual(
            result,
            Classification("Sin categoría", "fallback:no_reliable_match", "low"),
        )

    def test_representative_real_catalog_names_cover_the_taxonomy(self):
        cases = (
            ("Frutas y verduras", "FR AGUACATE XKG", {}),
            ("Frutas y verduras", "FR YUCAPELADA XKG", {}),
            ("Frutas y verduras", "FR PAPA LAVADA XKG", {}),
            ("Frutas y verduras", "FR PAQUETE PAPA LAVADA", {}),
            ("Frutas y verduras", "PAQUETE PAPA PASTUSA X 3KG", {}),
            ("Frutas y verduras", "FR PAQUETE HINOJO", {}),
            ("Carnes, pescados y embutidos", "ZENU SALCHICHON POLLO X 500 GR", {}),
            ("Comidas preparadas y congeladas", "CALYPSO CAZUELA DE MARISCOS X 500GR", {}),
            ("Huevos", "HUEVOS AA CUBETA X 30 UND", {}),
            ("Lácteos y refrigerados", "COLANTA YOGUR ENTERO MELOCOTON VASO 145 ML", {}),
            ("Panadería, arepas y repostería", "BIMBO PAN BLANCO X 500 GR", {}),
            ("Arroz, granos y pastas", "DIANA ARROZ PREMIUM X 1000 GR", {}),
            ("Cereales, harinas y mezclas", "ZUCARITAS KELLOGGS X 360 GR", {}),
            ("Aceites, azúcar, sal y panela", "ACEITE FINO GIRASOL X 3000 ML", {}),
            ("Untables y mermeladas", "MERMELADA DE FRESA X 250 GR", {}),
            ("Salsas, condimentos y sopas", "FRUCO SALSA DE TOMATE X 400 GR", {}),
            ("Enlatados y conservas", "AINOA ACEITUNAS RELLENAS DE PIMENTON X 180 GR", {}),
            ("Snacks y pasabocas", "KRAKS MANI LIMON X 132 GR", {}),
            ("Galletas y tostadas", "CLUB SOCIAL GALLETAS QUESO X 144 GR", {}),
            ("Dulces y chocolates", "BONBONBUM SANDIA SENSATIONS X 24 UND", {}),
            ("Helados y congelados", "HELADO DE VAINILLA X 1 L", {}),
            ("Café, chocolate e infusiones", "JUAN VALDEZ CAFE MOLIDO COLINA X 250 GR", {}),
            ("Bebidas en polvo", "TANG NARANJA BEBIDA EN POLVO X 20 GR", {}),
            ("Aguas", "MIXJOY AGUA CON COLAGENO TROPICAL X 300 ML", {}),
            ("Gaseosas, maltas y energizantes", "7UP X 250 ML", {}),
            ("Jugos y bebidas de fruta", "JUGO HIT MANGO X 500 ML", {}),
            ("Cervezas, vinos y licores", "CHIVAS REGAL BLENDED SCOTCH 13 YEARS X 700 ML", {}),
            ("Limpieza del hogar", "LA NACIONAL LIMPIA VIDRIOS X 500 ML", {}),
            ("Lavandería", "3D DETERGENTE EN POLVO BLANCO X 500 GR", {}),
            ("Ambientadores y control de plagas", "BON AIRE AEROSOL FRUTOS ROJOS X 400 ML", {}),
            ("Cuidado capilar", "SEDAL SHAMPOO CERAMIDAS X 340 ML", {}),
            ("Cuidado dental", "COLGATE CREMA DENTAL TRIPLE ACCION X 75 ML", {}),
            ("Cuidado femenino", "TOALLA NOSOTRAS INV RAPIGEL", {}),
            ("Cuidado personal y belleza", "POLVO COMPACTO SAMY X 11 GR", {}),
            ("Bebé", "BABYFRUIT COMPOTA DURAZNO X 120 G", {}),
            ("Salud y farmacia", "SINUTAB PLUS NS", {}),
            ("Mascotas", "SEMILLAS DE GIRASOL SUDES.PET X 500 GR", {}),
            ("Papel, desechables y cocina", "SERVILLETAS FAMILIA X 100 UND", {}),
            ("Papelería y escolar", "LETRA DE CAMBIO 50 HOJAS TAYDEM", {}),
            ("Ferretería, eléctrico y tecnología", "REGLETA ELECTRICA X 2FT CORTA", {}),
            ("Hogar y accesorios", "SERVILLETERO FIESTA", {}),
            ("Ropa interior", "BRASIER BEIGE TALLA 36", {}),
            ("Trajes de baño", "VESTIDO DE BAÑO ENTERIZO", {}),
            ("Piscina y acuáticos", "FLOTADOR RUEDA GRANDE", {}),
            ("Cigarrillos y encendedores", "ENCENDEDOR RECARGABLE", {}),
            ("Recargas y servicios", "RECARGA CELULAR 10000", {}),
            ("S.O.S Colombia", "AYUDA ESPECIAL", {"current_category_name": "S.O.S Colombia"}),
        )
        for expected, name, kwargs in cases:
            with self.subTest(expected=expected, name=name):
                self.assert_category(expected, name, **kwargs)

    def test_priority_rules_avoid_common_false_positives(self):
        cases = (
            ("Cuidado personal y belleza", "ACEITE COSMETICO CANNABIS X 100 ML"),
            ("Papelería y escolar", "SILICONA LIQUIDA POINTER X 60 ML"),
            ("Dulces y chocolates", "MENTA HELADA COLOMBINA X 100 UNI"),
            ("Salud y farmacia", "AGUA OXIGENADA X 120 ML"),
            ("Salsas, condimentos y sopas", "FRUCO SALSA DE TOMATE X 400 GR"),
            ("Cuidado capilar", "SHAMPOO MANZANA X 400 ML"),
            ("Cuidado dental", "PASTA DENTAL MENTA X 75 ML"),
            ("Lavandería", "JABON REY BARRA PARA ROPA"),
            ("Limpieza del hogar", "BOLSA DE BASURA NEGRA X 10 UND"),
            ("Mascotas", "COMIDA PARA PERRO SABOR POLLO X 1 KG"),
            ("Hogar y accesorios", "SERVILLETERO FIESTA"),
            ("Cuidado capilar", "PANTENE BIOTINAMINA 18 ML"),
        )
        for expected, name in cases:
            with self.subTest(name=name):
                self.assert_category(expected, name)

    def test_generic_clothes_and_toys_do_not_trigger_swimwear(self):
        for name in (
            "SET MUNECA CON ACCESORIOS Y VESTIDOS",
            "PANTALONETA DEPORTIVA HOMBRE",
            "TOP COCINA EN VIDRIO",
            "VESTIDO ROJO TALLA M",
        ):
            with self.subTest(name=name):
                result = classify_product(999999999, name)
                self.assertEqual(result.category, "Sin categoría")
                self.assertEqual(result.rule, "fallback:no_reliable_match")
                self.assertEqual(result.confidence, "low")

    def test_description_can_classify_and_unknowns_require_review(self):
        result = classify_product(1, "REFERENCIA 123", description="Champu para cabello seco")
        self.assertEqual(result.category, "Cuidado capilar")

        result = classify_product(2, "7702191161593", current_category_name="Accesorios")
        self.assertEqual(
            result,
            Classification("Sin categoría", "fallback:no_reliable_match", "low"),
        )

    def test_requested_fallback_and_priority_regressions(self):
        cases = (
            ("Sin categoría", "REFERENCIA TOTALMENTE DESCONOCIDA"),
            ("Limpieza del hogar", "FABULOSO BEBE X 1000 ML"),
            ("Mascotas", "MASCOTIKAS TETERO X 60 ML"),
            ("Cuidado personal y belleza", "TENA BASIC PANAL ADULTO"),
            ("Mascotas", "ATUN FELIX X 85 GR"),
            ("Hogar y accesorios", "VELON SAN MARTIN CON ENCENDEDOR"),
            ("Ferretería, eléctrico y tecnología", "VARTA LINTERNA RECARGABLE"),
            ("Limpieza del hogar", "AXION CREMA LIMON"),
            ("Bebé", "JOHNSONS SHAMPOO ORIGINAL X 200 ML"),
            ("Galletas y tostadas", "CLUB SOCIAL JAMON X 144 GR"),
            ("Panadería, arepas y repostería", "AREPA DE QUESO X 5"),
            ("Galletas y tostadas", "BRIDGE WAFER RON PASAS"),
            ("Dulces y chocolates", "HUEVO SORPRESA CHOCOLATE"),
            ("Cuidado personal y belleza", "SCHICK QUATRO"),
            ("Recargas y servicios", "DOMICILIO"),
            ("Cuidado dental", "ORALB CEPILLO PRO SALUD"),
            ("Cuidado capilar", "DOVE SH RECONSTRUCCION X 400 ML"),
            ("Salud y farmacia", "BUSCAPINA FEM X 4 TAB"),
            ("Salud y farmacia", "GUANTES DE EXAMEN LATEX TALLA M"),
            ("Cuidado personal y belleza", "PINZA PARA CABELLO GRANDE"),
            ("Limpieza del hogar", "TERGO GLOP PINO X 500 ML"),
            ("Papelería y escolar", "PINCEL ESCOLAR NUMERO 8"),
            ("Papel, desechables y cocina", "PALO PINCHO X 100 UND"),
            ("Dulces y chocolates", "PINPOP SABOR FRESA"),
            ("Lácteos y refrigerados", "ALPINETTE CHOCOLATE X 140 GR"),
            ("Lácteos y refrigerados", "ALPINA REGENERIS X 180 GR"),
            ("Lácteos y refrigerados", "ALQUERIA KUMIS X 1000 ML"),
            ("Lácteos y refrigerados", "COLANTA QUESITO X 250 GR"),
            ("Huevos", "HUEVO JUMBO X 30 UND"),
            ("Limpieza del hogar", "BOLSA NEGRA EXTRA JUMBO 90X120CM"),
            ("Helados y congelados", "VASO HELADO JUMBO X 1/2L"),
            ("Panadería, arepas y repostería", "BIMBO PONQUE CASERO DE LIMON 200 G"),
            ("Helados y congelados", "CASERO RON CON PASAS X 60G"),
            ("Aceites, azúcar, sal y panela", "DIANA ACEITE CON VITAMINAS X 900 ML"),
            ("Cereales, harinas y mezclas", "DIANA HARINA MAIZ BLANCO X 500 GR"),
            ("Salsas, condimentos y sopas", "VAN CAMPS SALSA DE TOMATE X 425 GR"),
            ("Salsas, condimentos y sopas", "SAN JORGE SALSA BBQ X 80 GR"),
            ("Snacks y pasabocas", "LA ESPECIAL AGUILA MANI CON LIMON X 35 GR"),
            ("Aceites, azúcar, sal y panela", "VIVA SOYA ACEITE VEGETAL X 500 ML"),
            ("Lácteos y refrigerados", "VIVA SOYA X 450 ML"),
            ("Lácteos y refrigerados", "BARRILETE LECHE SABORIZADA X 200 ML"),
            ("Helados y congelados", "BOCATTO BROWNIE Y SALSA DE CARAMELO X 93 GR"),
            ("Snacks y pasabocas", "RAMO PAPAS PAPRIKA X 105 G"),
            ("Ambientadores y control de plagas", "GLADE GEL FLORAL X 70 GR"),
            ("Lavandería", "VANISH GEL QUITAMANCHAS X 500 ML"),
            ("Cuidado personal y belleza", "VOGUE ESMALTE GEL ROJO"),
            ("Salud y farmacia", "APRONAX GEL X 30 GR"),
            ("Papelería y escolar", "LAPICERO GEL NEGRO"),
            ("Untables y mermeladas", "ALPINA AREQUIPE X 220 GR"),
            ("Lácteos y refrigerados", "ALQUERIA GELATINA FRESA X 90 GR"),
            ("Carnes, pescados y embutidos", "COLANTA MORCILLA ARTESANAL X 400 GR"),
            ("Dulces y chocolates", "TRULULU POSTOBON X 67 GR"),
            ("Papel, desechables y cocina", "POSTOBON VASO X UND"),
            ("Bebidas en polvo", "POSTOBON PANELADA LIMON X 25 GR"),
            ("Café, chocolate e infusiones", "POSTOBON HATSU TE AMARILLO X 400 ML"),
            ("Jugos y bebidas de fruta", "POSTOBON TUTTI FRUTTI SALPICON X 400 ML"),
            ("Aguas", "POSTOBON CRISTAL ALOE COCO X 330 ML"),
            ("Salud y farmacia", "CONDONES TE AMO X 3 UND"),
            ("Limpieza del hogar", "AJAX BICARBONATO NARANJA X 500 ML"),
            ("Snacks y pasabocas", "MANI CON LIMON Y PIMIENTA X 35 GR"),
            ("Cereales, harinas y mezclas", "TUTTI FRUTTI GELATINA DE FRESA X 8 GR"),
            ("Cereales, harinas y mezclas", "MAIZENA CRECI NUTRE SABOR AREQUIPE"),
            ("Panadería, arepas y repostería", "BIMBO BROWNIE AREQUIPE X 75 GR"),
            ("Lácteos y refrigerados", "BATILADO AREQUIPE X 82 GR"),
            ("Enlatados y conservas", "ATUM VAN CAMPS LOMITOS X 80 GR"),
            ("Café, chocolate e infusiones", "HINDU TE CALMA Y EQUILIBRIO"),
            ("Mascotas", "PURINA GATSY CARNE Y POLLO A LA JARDINERA X 500GR"),
            ("Mascotas", "NUTRISS ADULTO POLLO Y VEGETALES"),
            ("Mascotas", "MUAU PURE SNACK ATUN X 14 GR"),
            ("Mascotas", "ATUN FELI 156GR PATE PESCADO ATUN"),
            ("Mascotas", "DOW CHAOW ADULTOS CARNE Y POLLO X 1 KG"),
            ("Mascotas", "TUFFY CON PROTEINA DE POLLO X 500GR"),
            ("Mascotas", "DOGURMET ADULTO CARNE A LA PARRILLA X350GR"),
            ("Mascotas", "DONKAN CACHORROS X 800G"),
            ("Mascotas", "NUTRISS TROCITOS EN SALSA SABOR POLLO X 100GR"),
            ("Snacks y pasabocas", "RAMO PACHAS POLLO AGRIDULCE X 30G"),
            ("Snacks y pasabocas", "SUPER RICAS FOSFORITOS POLLO X 35GR"),
            ("Snacks y pasabocas", "SUPER RICA TROCITOS POLLO X 50 GR"),
            ("Arroz, granos y pastas", "SPAGHETTI SABOR A POLLO ASADO X250GR"),
            ("Arroz, granos y pastas", "NISSIN RAMEN POLLO X 85GR"),
            ("Arroz, granos y pastas", "CUP NOODLES POLLO PICANTE X68 GR"),
            ("Comidas preparadas y congeladas", "ZENU NUGGETS DE POLLO X 320 G"),
            ("Dulces y chocolates", "NUCITA NUGGETS X UND"),
            ("Lavandería", "JABON COCO EL ORIGINAL X180G"),
            ("Lavandería", "JABON LIQUIDO TIPO REY X500CM"),
            ("Lavandería", "JABON TOP TERRA AZUL X230G"),
            ("Untables y mermeladas", "CAMPI CON SAL X250GM"),
            ("Snacks y pasabocas", "LA ESPECIAL MANICERO SAL X 24GR"),
            ("Dulces y chocolates", "COFFE DELIGHT CAPPUCCINO 500G X 100UNDS"),
            ("Papel, desechables y cocina", "PALITOS PALETA X 50 UNI"),
            ("Papel, desechables y cocina", "PALOS PARA HELADO X 50 UNIDADES"),
            ("Papel, desechables y cocina", "FAMILIA GREEN PAPEL HIGIENICO X 4"),
            ("Aceites, azúcar, sal y panela", "GOURMET FAMILIA X 200ML"),
            ("Aceites, azúcar, sal y panela", "GOURNET FAMILIA 900 ML"),
            ("Untables y mermeladas", "LA BUENA CREMOSA X125GM"),
            ("Cervezas, vinos y licores", "LOS CUATES MANGO X 269ML"),
            ("Cervezas, vinos y licores", "LOS CUATES MANGO 473 ML"),
            ("Lavandería", "VEL ROSITA X300ML"),
            ("Café, chocolate e infusiones", "INSTACREMX4G"),
            ("Cuidado capilar", "NUTRIBELA10 TERMOPROTECCION X27ML"),
            ("Ferretería, eléctrico y tecnología", "SUPER GLUEX 8G"),
            ("Cereales, harinas y mezclas", "COMESTIBLES PIPE COCO GRANDE"),
            ("Cereales, harinas y mezclas", "CAROLINA CIRUELA SIN SEMILLA X 100 GR"),
            ("Cereales, harinas y mezclas", "BATI CREMAX50GR"),
            ("Cereales, harinas y mezclas", "LA GRANJA PAISA COCO NATURALX 50 GR"),
            ("Snacks y pasabocas", "SEMILLAS DE GIRASOL X 200GR"),
            ("Café, chocolate e infusiones", "Lyne Clasico x100g"),
            ("Galletas y tostadas", "CAPRI VAINILLA X 12 GR"),
            ("Cervezas, vinos y licores", "BLACKYWHITE 37.5CL"),
            ("Cuidado capilar", "GEL XTREME X27GR"),
            ("Cuidado personal y belleza", "DESEO MANZANA VERDE X110GR"),
            ("Cuidado personal y belleza", "PIOJITOS X PQ"),
            ("Ambientadores y control de plagas", "GEL MORA RADIANTE X 70 GR"),
            ("Lavandería", "PURO HORTENSIAS Y FLORES BLANCAS X 360GR"),
            ("Enlatados y conservas", "SABOR DEL PACIFICO COSERVAS DE PESCADO X155GR"),
            ("Panadería, arepas y repostería", "BIMBO SUPER HAMBURGUESA X 350G"),
            ("Snacks y pasabocas", "YUPI TOSTI PIZZA QUESO RANCHERO X 28 GR"),
            ("Hogar y accesorios", "MOLDE PARA LASAGNA N 16"),
            ("Arroz, granos y pastas", "DORIA LASAGNA X 400 GR"),
            ("Café, chocolate e infusiones", "CHOCOLATE CORONA CLAVOS Y CANELA X 200GR"),
            ("Lácteos y refrigerados", "ALPINA AVENA CANELA X 250 GR"),
            ("Dulces y chocolates", "BONBON BUM CON TAJIN MARACUYA X 17GR"),
            ("Enlatados y conservas", "IDEAL CONSERVAS DE PESCADO EN SALSA DE TOMATE"),
            ("Cervezas, vinos y licores", "REFAJO KOLA Y POLA X 330CM"),
            ("Café, chocolate e infusiones", "MILO ACTI GO CON MALTA X 100 GR"),
            ("Mascotas", "GALLETAS WAU SABOR POLLO X 80 GR"),
            ("Dulces y chocolates", "JELLY CIOSO HUEVO FRITO X 20G"),
            ("Arroz, granos y pastas", "DORIA MACARRONES CON QUESO X 53GR"),
            ("Bebé", "ARRURU TOALLITAS HUMEDAS AVENA Y KARITE"),
            ("Helados y congelados", "PALETA TOSH FRESA X 75 GR"),
            ("Ferretería, eléctrico y tecnología", "ACEITE 3 EN 1 MULTIPROPOSITO X 100ML"),
            ("Ambientadores y control de plagas", "PLUGINS ACEITE REPUESTO VAINILLA X 21ML"),
            ("Limpieza del hogar", "LAVALOZA DERSA REY X 450GR"),
            ("Salud y farmacia", "ACETATO DE ALUMINIO X 120ML"),
            ("Papel, desechables y cocina", "PORTACOMIDA DESECHABLE X 20 UND"),
            ("Bebidas en polvo", "SUN TEA LIMON X 12G"),
            ("Dulces y chocolates", "TRULULU HELADO DE FRESA X 54G"),
        )
        for expected, name in cases:
            with self.subTest(name=name):
                result = classify_product(999999999, name)
                self.assertEqual(result.category, expected, result)

        honey = classify_product(999999999, "EL PANAL MIEL DE ABEJA")
        self.assertNotEqual(honey.category, "Bebé")
