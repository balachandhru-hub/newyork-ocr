"""Phase 1 checks for the compliance gate and tenant-scoped store."""

import tempfile
import unittest
from pathlib import Path

from decimal import Decimal

from uae_einvoicing.engine import apply_gate, derive_flags, money
from uae_einvoicing.pipeline import execute_mapping
from uae_einvoicing.store import FLAG_RULES, Store, build_seed


class GateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "uae.json")

    def tearDown(self):
        self.directory.cleanup()

    def test_error_blocks_asp_readiness(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00042")
        self.assertEqual(txn["lifecycleStatus"], "COMPLIANCE_FAILED")
        self.assertEqual(txn["aspStatus"], "NOT_SUBMITTED")
        self.assertTrue(any(result["status"] == "FAILED" and result["severity"] == "ERROR" for result in txn["validationResults"]))

    def test_correction_can_clear_a_compliance_failure(self):
        meta = {"role": "UAE eInvoice Administrator", "ip": "127.0.0.1", "session": "test"}
        updated = self.store.correct_transaction("ten_five", "UAE-2026-00042", "seller.trn", "100234567890003", "TRN supplied by the hotel finance team", "tester", meta)
        self.assertEqual(updated["lifecycleStatus"], "READY_FOR_ASP")
        self.assertNotEqual(updated["lifecycleStatus"], "SUBMITTED")

    def test_error_exceptions_cannot_be_ignored(self):
        meta = {"role": "Exception Processor", "ip": "127.0.0.1", "session": "test"}
        with self.assertRaises(PermissionError):
            self.store.exception_action("ten_five", "EX-1001", "ignore", "processor", meta)

    def test_tenant_isolation(self):
        with self.assertRaises(KeyError):
            self.store.get_transaction("ten_harbour", "UAE-2026-00042")

    def test_secret_is_not_persisted(self):
        meta = {"role": "Integration Administrator", "ip": "127.0.0.1", "session": "test"}
        saved = self.store.save_connector("ten_five", {
            "connectorName": "OPERA KIT",
            "systemType": "OPERA KIT",
            "direction": "Outbound",
            "secret": "super-secret-value",
            "legalEntityId": "le_five_jv",
        }, "integrator", meta)
        self.assertNotIn("super-secret-value", str(saved))
        blob = Path(self.store.path).read_text()
        self.assertNotIn("super-secret-value", blob)
        self.assertTrue(saved["credentialReference"].startswith("secret://"))

    def test_export_flag_is_derived_not_hardcoded(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00046")
        export = next(flag for flag in txn["classification"] if flag["code"] == "EXPORT")
        self.assertTrue(export["derived"])
        zone = derive_flags(txn["canonical"], FLAG_RULES)
        self.assertFalse(next(flag for flag in zone if flag["code"] == "FREE_TRADE_ZONE")["derived"])

    def test_ready_invoice_passes_gate(self):
        seed = build_seed()
        txn = next(item for item in seed["transactions"] if item["id"] == "UAE-2026-00041")
        gated = apply_gate(txn, [rule for rule in seed["rules"] if rule["tenantId"] == "ten_five"])
        self.assertEqual(gated["complianceStatus"], "COMPLIANT")
        self.assertNotEqual(gated["lifecycleStatus"], "COMPLIANCE_FAILED")

    def test_mapping_version_activation_is_scoped(self):
        meta = {"role": "Integration Administrator", "ip": "127.0.0.1", "session": "test"}
        versioned = self.store.mapping_action("ten_five", "map_s4_tax_v1", "version", "integrator", meta)
        self.assertEqual(versioned["version"], 2)
        self.assertEqual(versioned["status"], "Inactive")
        active = self.store.mapping_action("ten_five", versioned["id"], "activate", "integrator", meta)
        self.assertEqual(active["status"], "Active")
        previous = next(item for item in self.store.data["mappings"] if item["id"] == "map_s4_tax_v1")
        self.assertEqual(previous["status"], "Inactive")

    def test_decimal_arithmetic_does_not_use_binary_float(self):
        self.assertEqual(money("0.10") + money("0.20"), Decimal("0.30"))
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00041")
        self.assertEqual(txn["complianceScore"]["status"], "COMPLIANT")
        self.assertTrue(txn["complianceScore"]["label"].endswith("COMPLIANT"))

    def test_one_error_fails_even_when_the_score_is_high(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00042")
        self.assertGreater(txn["complianceScore"]["percent"], 0)
        self.assertEqual(txn["complianceScore"]["status"], "FAILED")
        self.assertIn("FAILED", txn["complianceScore"]["label"])
        self.assertEqual(txn["lifecycleStatus"], "COMPLIANCE_FAILED")

    def test_transaction_type_code_is_derived_from_flags(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00047")
        self.assertEqual(txn["transactionType"]["code"], "10000000")
        export = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00046")
        self.assertEqual(export["transactionType"]["code"], "00000001")

    def test_credit_note_does_not_pass_on_invoice_rules_alone(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00044")
        self.assertEqual(txn["lifecycleStatus"], "COMPLIANCE_FAILED")
        self.assertTrue(any(result["ruleId"] == "UAE-CRN-001" and result["status"] == "FAILED" for result in txn["validationResults"]))

    def test_commercial_invoice_is_not_blocked_by_the_tax_rate_rule(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00049")
        self.assertFalse(any(result["ruleId"] == "UAE-TAX-001" and result["status"] == "FAILED" for result in txn["validationResults"]))
        self.assertTrue(any(item["code"] == "COMMERCIAL_INVOICE" for item in txn["transactionType"]["scenarios"]))

    def test_self_billing_is_classified(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00045")
        self.assertTrue(any(item["code"] == "SELF_BILLING" for item in txn["transactionType"]["scenarios"]))

    def test_correction_keeps_the_same_id_and_source_payload(self):
        meta = {"role": "Compliance Manager", "ip": "127.0.0.1", "session": "test"}
        txn = self.store.get_transaction("ten_five", "UAE-2026-00042")
        txn_row = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00042")
        txn_row["sourcePayload"] = {"HOTEL_TRN": ""}
        updated = self.store.correct_transaction("ten_five", "UAE-2026-00042", "seller.trn", "100234567890003", "Finance supplied the TRN", "manager", meta)
        self.assertEqual(updated["id"], "UAE-2026-00042")
        self.assertEqual(updated["sourcePayload"], {"HOTEL_TRN": ""})
        self.assertEqual(updated["lifecycleStatus"], "READY_FOR_ASP")

    def test_simulation_does_not_create_a_transaction_or_call_an_asp(self):
        meta = {"role": "Integration Administrator", "ip": "127.0.0.1", "session": "test"}
        before = [item["id"] for item in self.store.data["transactions"]]
        result = self.store.simulate("ten_five", {
            "legalEntityId": "le_five_jv",
            "sourceSystem": "OPERA",
            "documentType": "Tax Invoice",
            "payload": {"FOLIO_NO": "SIM-100", "HOTEL_TRN": "100234567890003", "CUSTOMERVAT": "100555666777003", "CURRENCY": "AED", "NETAMOUNT": "1000.00", "BILLINGDATE": "20261014"},
        }, "integrator", meta)
        after = [item["id"] for item in self.store.data["transactions"]]
        self.assertEqual(before, after)
        self.assertEqual(result["label"], "SIMULATION ONLY")
        self.assertFalse(result["persisted"])
        self.assertNotIn("taxilla", str(result).lower())

    def test_mapping_execution_translates_a_tax_code(self):
        outputs = execute_mapping(
            {"TAX_CODE": "A5"},
            [{"sourceField": "TAX_CODE", "targetField": "tax.category", "mappingType": "Code Translation", "codeList": "taxCode"}],
            self.store.data["codeLists"],
        )
        self.assertEqual(outputs["tax.category"]["finalValue"], "S")

    def test_invalid_trn_is_not_copied_into_tin(self):
        txn = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00042")
        tin = txn["canonical"]["seller"].get("tin")
        self.assertTrue(tin is None or tin.get("finalValue") in (None, ""))

    def test_production_activation_is_blocked_when_configuration_is_incomplete(self):
        meta = {"role": "Integration Administrator", "ip": "127.0.0.1", "session": "test"}
        with self.assertRaises(PermissionError):
            self.store.save_legal_entity("ten_five", {
                "entityName": "Incomplete",
                "legalName": "Incomplete LLC",
                "entityCode": "INC",
                "trn": "123",
                "status": "Production",
                "country": "AE",
                "defaultCurrency": "AED",
            }, "integrator", meta)

    def test_activating_a_draft_profile_keeps_historical_versions(self):
        meta = {"role": "Compliance Administrator", "ip": "127.0.0.1", "session": "test"}
        before = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00041")["complianceProfileId"]
        self.store.activate_profile("ten_five", "prof_uae_2026_2", "admin", meta)
        after = next(item for item in self.store.data["transactions"] if item["id"] == "UAE-2026-00041")
        self.assertEqual(after["complianceProfileId"], before)
        self.assertEqual(after["complianceVersion"], "2026.1")
        draft = next(item for item in self.store.data["profiles"] if item["id"] == "prof_uae_2026_2")
        self.assertEqual(draft["status"], "ACTIVE")

    def test_mapping_readiness_stays_with_the_legal_entity(self):
        entities = {item["entity"]: item for item in self.store.legal_entity_readiness("ten_five")}
        self.assertNotEqual(entities["FIVE Jumeirah Village"]["mappingPercent"], entities["FIVE Palm Jumeirah"]["mappingPercent"])
        sources = {item["sourceSystem"]: item for item in self.store.mapping_readiness("ten_five")}
        self.assertEqual(sources["SAP S/4HANA"]["mandatoryPercent"], 100)
        self.assertEqual(sources["SAP S/4HANA"]["productionReadiness"], "READY")
        self.assertEqual(sources["OPERA"]["mandatoryPercent"], 100)
        self.assertEqual(sources["OPERA"]["productionReadiness"], "NOT READY")

    def test_tenant_cannot_read_another_tenants_mapping(self):
        mappings = self.store.scoped("mappings", "ten_harbour")
        self.assertTrue(all(item["tenantId"] == "ten_harbour" for item in mappings))
        self.assertFalse(any(item["id"] == "map_s4_tax_v1" for item in mappings))


if __name__ == "__main__":
    unittest.main()
