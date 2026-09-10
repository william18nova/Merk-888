from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class SaleDraftCacheContractTests(SimpleTestCase):
    """Contrato de seguridad y recuperacion del borrador local de ventas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        app_dir = Path(settings.BASE_DIR) / "mainApp"
        cls.script = (
            app_dir / "static" / "javascript" / "generar_venta.js"
        ).read_text(encoding="utf-8")
        cls.template = (
            app_dir / "templates" / "generar_venta.html"
        ).read_text(encoding="utf-8")
        cls.styles = (
            app_dir / "static" / "css" / "generar_venta.css"
        ).read_text(encoding="utf-8")
        cls.views = (app_dir / "views.py").read_text(encoding="utf-8")

    def _function_source(self, name, next_name):
        start = self.script.index(f"function {name}")
        end = self.script.index(f"function {next_name}", start)
        return self.script[start:end]

    def test_template_offers_compact_draft_manager_and_revalidation(self):
        for fragment in (
            'id="venta-draft-center"',
            'id="venta-draft-toggle"',
            'id="venta-draft-count"',
            'id="venta-draft-panel"',
            'id="venta-draft-list"',
            'id="venta-draft-close"',
            'id="venta-draft-new"',
            'id="venta-draft-live"',
            'id="venta-draft-save-status"',
            'id="venta-draft-retry-validation"',
            "window.ventaUsuarioId",
            "window.ventaTurnoId",
        ):
            self.assertIn(fragment, self.template)
        self.assertRegex(self.template, r"generar_venta\.css' %\}\?v=\d+")
        self.assertRegex(self.template, r"generar_venta\.js' %\}\?v=\d+")
        self.assertIn(".venta-draft-toggle", self.styles)
        self.assertIn(".venta-draft-panel", self.styles)
        self.assertIn(".venta-draft-item", self.styles)
        self.assertIn("var(--text-muted)", self.styles)

    def test_v2_scope_and_unique_cart_key_include_all_cashbox_dimensions(self):
        self.assertIn("const SALE_DRAFT_VERSION = 2", self.script)
        self.assertIn('|| "sin_punto"', self.script)
        self.assertIn('|| "sin_turno"', self.script)
        self.assertIn("let saleDraftActiveID", self.script)
        self.assertIn("function saleDraftScopeStorageKey(", self.script)
        self.assertIn(
            ":u${scope.userId}:s${scope.sucursalId}:p${normalizedPoint}:t${scope.turnoId}",
            self.script,
        )
        storage_key = self._function_source(
            "saleDraftStorageKey()", "saleDraftStorageKeyForPoint("
        )
        self.assertIn("saleDraftScopeStorageKey()", storage_key)
        self.assertIn("saleDraftActiveID", storage_key)
        self.assertIn(":d${saleDraftActiveID}", storage_key)
        for scope_check in (
            'String(raw.user_id || "") !== scope.userId',
            'String(raw.sucursal_id || "") !== scope.sucursalId',
            'String(raw.puntopago_id || "") !== scope.puntoPagoId',
            'String(raw.turno_id || "") !== scope.turnoId',
        ):
            self.assertIn(scope_check, self.script)
        self.assertIn("puntopago_id: scope.puntoPagoId", self.script)
        self.assertIn("turno_id: scope.turnoId", self.script)
        self.assertIn("draft_id:", self.script)
        self.assertIn("storage_key: key", self.script)

    def test_drafts_expire_are_cleaned_and_oversized_payloads_are_rejected(self):
        self.assertIn("const SALE_DRAFT_TTL_MS = 24 * 60 * 60 * 1000", self.script)
        cleanup = self._function_source(
            "cleanupExpiredSaleDrafts()", "clearSaleDraftForCurrentScope("
        )
        self.assertIn('key.startsWith("nova:venta-draft:")', cleanup)
        self.assertIn("now - updatedAt > SALE_DRAFT_TTL_MS", cleanup)
        self.assertIn("updatedAt - now > 5 * 60 * 1000", cleanup)
        self.assertIn("removeSaleDraftByKey(key)", cleanup)
        self.assertIn("cleanupExpiredSaleDrafts();", self.script)
        self.assertIn("raw.items.length > SALE_DRAFT_MAX_ITEMS", self.script)
        self.assertIn("productos.length > SALE_DRAFT_MAX_ITEMS", self.script)

    def test_autosave_is_debounced_and_storage_failures_do_not_break_sales(self):
        self.assertIn("const SALE_DRAFT_SAVE_DELAY_MS = 220", self.script)
        self.assertIn("function scheduleSaleDraftSave()", self.script)
        self.assertIn("localStorage.setItem(key, JSON.stringify(payload))", self.script)
        self.assertIn("No se pudo guardar el borrador en este navegador", self.script)
        self.assertIn('window.addEventListener("pagehide"', self.script)
        self.assertNotIn("navigator.serviceWorker", self.script)

    def test_pending_draft_never_blocks_editing_or_charging(self):
        manager = self._function_source(
            "renderSaleDraftManager()", "offerSaleDraftForCurrentScope()"
        )
        persist = self._function_source("persistSaleDraftNow()", "scheduleSaleDraftSave()")
        self.assertIn("pendingSaleDraft = drafts[0]", manager)
        self.assertNotIn("setSaleDraftEditingBlocked(true)", manager)
        self.assertNotIn("Primero elige Recuperar o Descartar", self.script)
        self.assertNotIn("Elige Recuperar o Descartar antes de iniciar otra venta", persist)
        if "function guardSaleDraftEditing()" in self.script:
            guard = self._function_source(
                "guardSaleDraftEditing()", "setSaleDraftValidation("
            )
            self.assertIn("return true", guard)
            self.assertNotIn("alert(", guard)
            self.assertNotIn("return false", guard)
        self.assertIn('$inpCliente.on("input.employeeDiscount"', self.script)
        self.assertIn('$("#venta-draft-list").on("click", ".js-draft-restore"', self.script)
        self.assertIn('$("#venta-draft-list").on("click", ".js-draft-discard"', self.script)

    def test_starting_new_sale_preserves_pending_draft_under_its_unique_key(self):
        self.assertIn("function continueWithNewSale()", self.script)
        start_new = self._function_source(
            "continueWithNewSale()", "applySaleDraftPaymentState("
        )
        self.assertIn("listManagedSaleDrafts()", start_new)
        self.assertIn("saleDraftActiveID", start_new)
        self.assertIn("createSaleDraftID()", start_new)
        self.assertNotIn("removeSaleDraftByKey", start_new)
        self.assertIn(
            '$("#venta-draft-new").on("click", continueWithNewSale)',
            self.script,
        )

    def test_all_drafts_in_same_scope_are_listed_without_overwriting_each_other(self):
        self.assertIn("function listSaleDraftsForCurrentScope()", self.script)
        listed = self._function_source(
            "listSaleDraftsForCurrentScope()", "readSaleDraftForCurrentScope()"
        )
        self.assertIn("localStorage.length", listed)
        self.assertIn("localStorage.key(index)", listed)
        self.assertIn("saleDraftScopeStorageKey()", listed)
        self.assertIn("isSaleDraftKeyForCurrentScope(key)", listed)
        self.assertIn(".map(readSaleDraftByKey)", listed)
        self.assertIn(".sort(", listed)
        self.assertIn("candidate === scopeKey", self.script)
        self.assertIn('candidate.startsWith(`${scopeKey}:d`)', self.script)

    def test_compact_manager_lists_every_pending_draft_and_excludes_open_cart(self):
        managed = self._function_source(
            "listManagedSaleDrafts()", "findManagedSaleDraft(storageKey)"
        )
        rendered = self._function_source(
            "renderSaleDraftManager()", "offerSaleDraftForCurrentScope()"
        )
        self.assertIn("listSaleDraftsForCurrentScope()", managed)
        self.assertIn("draft.storage_key !== activeKey", managed)
        self.assertIn("saleDraftRecoverableKeys.has(draft.storage_key)", managed)
        self.assertIn("for (const draft of drafts)", rendered)
        self.assertIn("venta-draft-item", rendered)
        self.assertIn("js-draft-restore", rendered)
        self.assertIn("js-draft-discard", rendered)
        self.assertIn("saleDraftLastAnnouncedCount", rendered)

    def test_tab_ownership_only_detects_remote_changes_to_the_active_cart(self):
        persist = self._function_source("persistSaleDraftNow()", "scheduleSaleDraftSave()")
        self.assertIn("const saleDraftTabID", self.script)
        self.assertIn("owner_tab_id: saleDraftTabID", persist)
        self.assertIn('window.addEventListener("storage"', self.script)
        storage_start = self.script.index('window.addEventListener("storage"')
        storage_source = self.script[storage_start:]
        self.assertIn("event.key !== saleDraftStorageKey()", storage_source)
        self.assertIn("incomingOwner !== saleDraftTabID", self.script)
        self.assertIn("Otra pestaña modificó la venta pendiente", self.script)

    def test_restore_revalidates_quantity_stock_price_and_exact_client(self):
        verify = self._function_source(
            "verifyRestoredSaleDraftItem(item)", "revalidateRestoredSaleDraft(draft)"
        )
        revalidate = self._function_source(
            "revalidateRestoredSaleDraft(draft)",
            "restorePendingSaleDraft(draftOverride = null)",
        )
        client_fetch = self._function_source(
            "fetchSaleDraftClientById(clientId)", "setClienteCache(key, items)"
        )

        self.assertIn("producto_id: item.producto_id", verify)
        self.assertIn("cantidad: item.cantidad", verify)
        self.assertIn("sucursal_id: sucursalID", verify)
        self.assertIn("Number(response.precio_unitario)", revalidate)
        self.assertIn("Number(response.cantidad_disponible)", revalidate)
        self.assertIn("available < item.cantidad", revalidate)
        self.assertIn("!response.exists", revalidate)
        self.assertIn("setRowPriceUI($row, price)", revalidate)
        self.assertIn("removeRowByPid(item.producto_id)", revalidate)
        self.assertIn("fetchSaleDraftClientById(draft.client.id)", revalidate)
        self.assertIn("applySelectedClientItem(restoredClient)", revalidate)
        self.assertIn("else clearSelectedClient()", revalidate)
        self.assertIn("cliente_id: id, limit: 1", client_fetch)
        self.assertIn('.find((row) => String(row?.id || "") === id)', client_fetch)

        view_start = self.views.index("class ClienteAutocompleteView")
        view_end = self.views.index("class ", view_start + 10)
        view_source = self.views[view_start:view_end]
        self.assertIn('if "cliente_id" in request.GET', view_source)
        self.assertIn("qs = qs.filter(pk=exact_cliente_id)", view_source)
        self.assertIn("start, end = 0, 1", view_source)

    def test_restore_clears_authorizations_and_nequi_link(self):
        restore = self._function_source(
            "restorePendingSaleDraft(draftOverride = null)",
            "discardPendingSaleDraft(draftOverride = null)",
        )
        for fragment in (
            '$("#employee-password-input").val("")',
            '$hidEmpleadoPassword.val("")',
            '$("#merk2888-password-input").val("")',
            '$hidMerk2888Password.val("")',
            '$hidNequiNotification.val("")',
        ):
            self.assertIn(fragment, restore)
        self.assertIn("void revalidateRestoredSaleDraft(draft)", restore)
        self.assertIn("saleDraftPaymentState = null", restore)
        self.assertNotIn("sanitizeSaleDraftPayment(draft.payment)", restore)

    def test_charge_button_is_blocked_only_during_revalidation_or_invalid_state(self):
        self.assertIn(
            'data-server-enabled="{% if venta_habilitada %}1{% else %}0{% endif %}"',
            self.template,
        )
        refresh = self._function_source(
            "refreshSaleDraftGenerateButton()", "setSaleDraftValidation("
        )
        for condition in (
            "!serverEnabled",
            "saleDraftValidationPending",
            "!!saleDraftValidationError",
            "saleDraftInvalidProductIds.size > 0",
        ):
            self.assertIn(condition, refresh)
        self.assertNotIn("pendingSaleDraft", refresh)
        self.assertNotIn("saleDraftEditingBlocked", refresh)
        self.assertIn('$button.prop("disabled", blocked)', refresh)
        self.assertGreaterEqual(self.script.count("if (!saleDraftReadyToCharge()) return"), 2)

    def test_restore_consumes_closed_draft_only_after_saving_the_new_cart(self):
        self.assertIn("function continueWithNewSale()", self.script)
        restore = self._function_source(
            "restorePendingSaleDraft(draftOverride = null)",
            "discardPendingSaleDraft(draftOverride = null)",
        )
        discard_start = self.script.index(
            "function discardPendingSaleDraft(draftOverride = null)"
        )
        discard_end = self.script.index("const PROMO_BAG_21", discard_start)
        discard = self.script[discard_start:discard_end]
        self.assertIn("selectedDraft.storage_key", restore)
        self.assertIn("readSaleDraftByKey(sourceKey)", restore)
        self.assertIn("saleDraftActiveID = createSaleDraftID()", restore)
        self.assertIn('saleDraftSubmittedKey = ""', restore)
        self.assertIn("saleDraftSaleConfirmed = false", restore)
        self.assertNotIn("draft.draft_id", restore)
        self.assertNotIn("saleDraftIDFromStorageKey", restore)
        self.assertIn("saleDraftLifecycle.withClosedDraft", restore)
        self.assertIn("const saved = persistSaleDraftNow()", restore)
        self.assertIn("!saved || !removeSaleDraftByKey(sourceKey)", restore)
        self.assertIn("saleDraftRecoverableKeys.delete(sourceKey)", restore)
        self.assertIn("carrito vacío", restore)
        self.assertLess(restore.index("const saved = persistSaleDraftNow()"), restore.index("removeSaleDraftByKey(sourceKey)"))
        self.assertNotIn("saleDraftAllowTakeoverOnce", self.script)
        self.assertIn("draftOverride || pendingSaleDraft", discard)
        self.assertIn("draft.storage_key", discard)
        self.assertIn("removeSaleDraftByKey", discard)
        self.assertNotIn("removeSaleDraftByKey(saleDraftStorageKey())", discard)

    def test_presence_is_loaded_before_sales_and_only_page_exit_releases_it(self):
        lifecycle = self.template.index("javascript/sale_draft_lifecycle.js")
        sale_script = self.template.index("javascript/generar_venta.js")
        self.assertLess(lifecycle, sale_script)
        self.assertIn("const saleDraftTabID = createSaleDraftID()", self.script)
        self.assertIn("saleDraftLifecycle?.openOwners()", self.script)
        self.assertIn("!owners.has(draft.owner_tab_id)", self.script)
        self.assertIn("suspendSaleDraftPage();", self.script)
        self.assertIn("void resumeSaleDraftPage();", self.script)
        self.assertIn("saleDraftPageHidden", self.script)
        pagehide = self.script.index('window.addEventListener("pagehide"')
        self.assertIn("suspendSaleDraftPage()", self.script[pagehide:pagehide + 140])
        beforeunload = self.script.index('window.addEventListener("beforeunload"')
        self.assertNotIn("suspendSaleDraftPage()", self.script[beforeunload:pagehide])

    def test_recovery_does_not_interrupt_charges_or_ignore_uncertain_submissions(self):
        restore = self._function_source(
            "restorePendingSaleDraft(draftOverride = null)", "discardPendingSaleDraft(draftOverride = null)",
        )
        self.assertIn("saleSubmitting || confirmSubmitting || saleDraftSaleConfirmed || saleDraftRestoring || saleDraftValidationPending", restore)
        self.assertIn('draft.status === "submission_pending"', restore)
        self.assertIn("!confirm(", restore)
        self.assertIn("NO está facturada", restore)

    def test_serialized_payload_excludes_credentials_otp_tokens_and_nequi_link(self):
        persist = self._function_source("persistSaleDraftNow()", "scheduleSaleDraftSave()")
        payload_start = persist.index("const payload = {")
        payload_end = persist.index("\n    };", payload_start)
        payload_source = persist[payload_start:payload_end]

        for forbidden in (
            "empleado_password",
            "employee-password-input",
            "codigo_descuento_merk2888",
            "merk2888-password-input",
            "nequi_notificacion_id",
            "selectedNequiPayment",
            "POS_AGENT_TOKEN",
            "csrfmiddlewaretoken",
            "password",
            "token",
            "documento",
        ):
            self.assertNotIn(forbidden, payload_source)

        for allowed in (
            "items,",
            "client: captureSaleDraftClient()",
            "payment: sanitizeSaleDraftPayment",
            "owner_tab_id: saleDraftTabID",
        ):
            self.assertIn(allowed, payload_source)

    def test_draft_is_deleted_only_after_server_success_using_captured_key(self):
        submit_start = self.script.index('$("#venta-form").off("submit")')
        submit_end = self.script.index("/* ================== Atajos Ctrl", submit_start)
        submit = self.script[submit_start:submit_end]

        capture = submit.index("saleDraftSubmittedKey = saleDraftStorageKey()")
        pending = submit.index("saleDraftSubmissionPending = true", capture)
        response_guard = submit.index("if (!r || !r.success)", pending)
        confirmed = submit.index("saleDraftSaleConfirmed = true", response_guard)
        clear = submit.index("clearSaleDraftForCurrentScope(saleDraftSubmittedKey)", confirmed)
        self.assertLess(capture, pending)
        self.assertLess(response_guard, confirmed)
        self.assertLess(confirmed, clear)

        before_success = submit[:confirmed]
        self.assertNotIn("clearSaleDraftForCurrentScope(saleDraftSubmittedKey)", before_success)
        network_failure = submit[submit.index(".catch(() =>", clear):]
        self.assertIn("saleDraftSubmissionPending = true", network_failure)
        self.assertIn("persistSaleDraftNow()", network_failure)
        self.assertIn("revisa Visualizar ventas antes de reintentar", network_failure)
        self.assertNotIn("clearSaleDraftForCurrentScope", network_failure)

    def test_unload_preserves_cache_instead_of_clearing_the_cart(self):
        start = self.script.index('window.addEventListener("beforeunload"')
        end = self.script.index('window.addEventListener("pagehide"', start)
        unload_source = self.script[start:end]
        self.assertIn("persistSaleDraftNow()", unload_source)
        self.assertNotIn("clearCartAndTotals()", unload_source)
