from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from .models import (
    ConceptoEgreso, Egreso, MetodoPago, Rol, TelegramAccionPendiente,
    TelegramActualizacion, TelegramUsuario, Usuario,
)
from .services.operational_expenses import find_similar_expense_concepts
from .services.telegram_bot import _handle_callback, build_reply, tool_prepare_expense


class TelegramExpenseConceptTests(TestCase):
    def setUp(self):
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Prueba conceptos", rolid=role)
        self.profile = TelegramUsuario.objects.create(
            usuario=self.user, telegram_user_id=9101, telegram_chat_id=9101,
        )
        MetodoPago.objects.create(
            codigo="efectivo", nombre="Efectivo", activo=True,
            es_efectivo=True, es_sistema=True,
        )
        self.existing = ConceptoEgreso.objects.create(nombre="COCA-COLA")
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())

    def propose(self, concept="cocacola"):
        reply = tool_prepare_expense(self.profile, {
            "concepto": concept, "monto": "1", "medio_pago": "efectivo",
        })
        return TelegramAccionPendiente.objects.latest("creado_en"), reply

    def callback(self, action, verb="confirm", option=None, profile=None):
        suffix = "" if option is None else f":{option}"
        update = SimpleNamespace(
            texto=f"{verb}:{action.pk}{suffix}", callback_query_id="test-callback",
        )
        return _handle_callback(update, profile or self.profile, self.client_stub)

    def test_similar_names_ignore_accents_separators_and_small_typos(self):
        for text in ("cocacola", "coca cola", "cóca-cola", "coca colla"):
            with self.subTest(text=text):
                self.assertEqual(find_similar_expense_concepts(text)[0]["id"], self.existing.pk)
        self.assertEqual(find_similar_expense_concepts("ARRIENDO"), [])
        self.assertEqual(find_similar_expense_concepts("CO"), [])

    def test_suggestions_are_bounded_and_exact_name_is_first(self):
        exact = ConceptoEgreso.objects.create(nombre="COCACOLA")
        for index in range(8):
            ConceptoEgreso.objects.create(nombre=f"COCACOLA {index}")
        suggestions = find_similar_expense_concepts("cocacola")
        self.assertEqual(len(suggestions), 5)
        self.assertEqual(suggestions[0]["id"], exact.pk)

    def test_proposal_offers_existing_and_new_without_creating_records(self):
        action, reply = self.propose()
        self.assertEqual(reply.intent, "seleccionar_concepto_pago")
        self.assertIn("COCA-COLA", reply.text)
        self.assertIn("COCACOLA", reply.text)
        buttons = [button for row in reply.reply_markup["inline_keyboard"] for button in row]
        self.assertTrue(any(button["callback_data"] == f"concept:{action.pk}:0" for button in buttons))
        self.assertTrue(any(button["callback_data"] == f"newconcept:{action.pk}" for button in buttons))
        self.assertTrue(all(len(button["callback_data"].encode("utf-8")) <= 64 for button in buttons))
        self.assertEqual(Egreso.objects.count(), 0)
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_existing_choice_requires_confirmation_and_is_idempotent(self):
        action, _ = self.propose()
        reply = self.callback(action, "concept", 0)
        self.assertEqual(reply.intent, "preparar_registro_pago")
        self.assertIn("COCA-COLA", reply.text)
        self.assertFalse(Egreso.objects.exists())
        action.refresh_from_db()
        self.assertEqual(action.argumentos["concepto_id"], self.existing.pk)
        self.assertEqual(action.argumentos["monto"], "1.00")
        self.assertEqual(action.argumentos["medio_pago"], "efectivo")
        self.callback(action)
        self.callback(action)
        self.assertEqual(Egreso.objects.count(), 1)
        expense = Egreso.objects.get()
        self.assertEqual(expense.concepto, self.existing)
        self.assertEqual(expense.monto, Decimal("1.00"))
        self.assertEqual(expense.medio_pago, "efectivo")
        self.assertEqual(expense.registrado_por, self.user)
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_new_choice_creates_uppercase_concept_only_on_confirmation(self):
        action, _ = self.propose()
        reply = self.callback(action, "newconcept")
        self.assertIn("Registrar COCACOLA", reply.text)
        self.assertEqual(ConceptoEgreso.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())
        self.callback(action)
        self.callback(action)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Egreso.objects.get().concepto.nombre, "COCACOLA")
        self.assertEqual(ConceptoEgreso.objects.count(), 2)

    def test_exact_name_goes_directly_to_confirmation_and_reuses_id(self):
        action, reply = self.propose("  coca-cola  ")
        self.assertEqual(reply.intent, "preparar_registro_pago")
        self.assertEqual(action.argumentos["concepto_id"], self.existing.pk)
        self.callback(action)
        self.assertEqual(Egreso.objects.get().concepto, self.existing)

    def test_unrelated_name_uses_original_confirmation_flow(self):
        action, reply = self.propose("  servicio   de agua  ")
        self.assertEqual(reply.intent, "preparar_registro_pago")
        self.callback(action)
        self.assertEqual(Egreso.objects.get().concepto.nombre, "SERVICIO DE AGUA")

    def test_confirmation_cannot_skip_pending_concept_choice(self):
        action, _ = self.propose()
        reply = self.callback(action)
        self.assertEqual(reply.intent, "seleccionar_concepto_pago")
        self.assertFalse(Egreso.objects.exists())
        action.refresh_from_db()
        self.assertTrue(action.argumentos["concepto_eleccion_pendiente"])

    def test_cancelled_choice_cannot_be_used(self):
        action, _ = self.propose()
        self.callback(action, "cancel")
        self.callback(action, "newconcept")
        self.callback(action)
        action.refresh_from_db()
        self.assertEqual(action.estado, "CANCELADA")
        self.assertFalse(Egreso.objects.exists())
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_invalid_options_do_not_modify_proposal(self):
        action, _ = self.propose()
        for verb, option in (("concept", 99), ("concept", None), ("newconcept", 0), ("concept", -1)):
            with self.subTest(verb=verb, option=option):
                self.callback(action, verb, option)
                action.refresh_from_db()
                self.assertTrue(action.argumentos["concepto_eleccion_pendiente"])
                self.assertFalse(Egreso.objects.exists())

    def test_other_users_cannot_choose_or_confirm(self):
        other_user = Usuario.objects.create_user("Otro usuario")
        other = TelegramUsuario.objects.create(
            usuario=other_user, telegram_user_id=9102, telegram_chat_id=9102,
        )
        action, _ = self.propose()
        for verb, option in (("concept", 0), ("newconcept", None), ("confirm", None)):
            reply = self.callback(action, verb, option, profile=other)
            self.assertIn("otra cuenta", reply.text)
        action.refresh_from_db()
        self.assertTrue(action.argumentos["concepto_eleccion_pendiente"])
        self.assertFalse(Egreso.objects.exists())

    def test_choices_do_not_extend_expiry(self):
        action, _ = self.propose()
        expires = action.vence_en
        self.callback(action, "concept", 0)
        action.refresh_from_db()
        self.assertEqual(action.vence_en, expires)
        action.vence_en = timezone.now()
        action.save(update_fields=["vence_en"])
        self.callback(action)
        action.refresh_from_db()
        self.assertEqual(action.estado, "EXPIRADA")
        self.assertFalse(Egreso.objects.exists())

    def test_expired_proposal_cannot_select_new_concept(self):
        action, _ = self.propose()
        action.vence_en = timezone.now()
        action.save(update_fields=["vence_en"])
        self.callback(action, "newconcept")
        action.refresh_from_db()
        self.assertEqual(action.estado, "EXPIRADA")
        self.assertTrue(action.argumentos["concepto_eleccion_pendiente"])

    def test_old_choice_button_cannot_change_concept_after_confirmation_is_shown(self):
        action, _ = self.propose()
        self.callback(action, "concept", 0)
        self.callback(action, "newconcept")
        self.callback(action)
        self.assertEqual(Egreso.objects.get().concepto, self.existing)
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_deleted_suggestion_is_not_silently_recreated(self):
        action, _ = self.propose()
        self.existing.delete()
        reply = self.callback(action, "concept", 0)
        self.assertIn("ya no existe", reply.text)
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(ConceptoEgreso.objects.exists())

    def test_renamed_selected_concept_blocks_final_payment(self):
        action, _ = self.propose()
        self.callback(action, "concept", 0)
        self.existing.nombre = "OTRO CONCEPTO"
        self.existing.save()
        reply = self.callback(action)
        self.assertIn("cambió", reply.text)
        self.assertFalse(Egreso.objects.exists())
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_permissions_are_checked_again_before_selection_and_confirmation(self):
        action, _ = self.propose()
        self.user.is_active = False
        for verb, option in (("concept", 0), ("confirm", None)):
            with self.assertRaises(PermissionDenied):
                self.callback(action, verb, option)
        self.assertFalse(Egreso.objects.exists())

    def test_payment_method_is_revalidated_after_selection(self):
        action, _ = self.propose()
        self.callback(action, "newconcept")
        MetodoPago.objects.filter(pk="efectivo").update(activo=False)
        reply = self.callback(action)
        self.assertIn("desactivado", reply.text)
        self.assertFalse(Egreso.objects.exists())
        self.assertEqual(ConceptoEgreso.objects.count(), 1)

    def test_text_and_transcribed_voice_both_use_suggestions(self):
        for number, kind in enumerate(("TEXTO", "VOZ"), start=80001):
            with self.subTest(kind=kind):
                text = "Pago de 1 en efectivo a cocacola"
                update = TelegramActualizacion.objects.create(
                    update_id=number, telegram_user_id=9101, telegram_chat_id=9101,
                    chat_type="private", tipo=kind,
                    texto=text if kind == "TEXTO" else "",
                    transcripcion=text if kind == "VOZ" else "",
                )
                with patch("mainApp.services.telegram_bot._intelligent_function_call", return_value=(
                    "preparar_registro_pago",
                    {"concepto": "cocacola", "monto": 1, "medio_pago": "efectivo"}, "",
                )):
                    reply = build_reply(update, self.client_stub)
                self.assertEqual(reply.intent, "seleccionar_concepto_pago")
                self.assertIn("COCA-COLA", reply.text)
        self.assertFalse(Egreso.objects.exists())
