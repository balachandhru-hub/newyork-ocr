"""Phase 3 onboarding: resolution, ingestion, duplicates and tenant scope."""

import tempfile
import unittest
from pathlib import Path

from uae_einvoicing.store import Store


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "uae.json")
        self.meta = {"role": "Integration Administrator", "ip": "127.0.0.1", "session": "test"}

    def tearDown(self):
        self.directory.cleanup()

    def test_opera_sample_reaches_ready_for_asp_once(self):
        first = self.store.ingest_request("ten_five", {"fixture": "opera-b2b"}, "integrator", self.meta)
        second = self.store.ingest_request("ten_five", {"fixture": "opera-b2b"}, "integrator", self.meta)
        self.assertEqual(first["lifecycleStatus"], "READY_FOR_ASP")
        self.assertTrue(first["regulatoryInvoiceCreated"])
        self.assertFalse(first["transaction"]["aspStatus"] == "SUBMITTED")
        self.assertEqual(second["idempotent"], True)
        self.assertEqual(second["transactionId"], first["transactionId"])
        self.assertEqual(first["transaction"]["entityResolution"]["status"], "RESOLVED")
        self.assertEqual(first["transaction"]["legalEntityId"], "le_five_jv")
        self.assertNotIn("taxilla", str(first).lower())

    def test_sap_invoice_uses_existing_fields_and_passes(self):
        result = self.store.ingest_request("ten_five", {"fixture": "sap-b2b"}, "integrator", self.meta)
        self.assertEqual(result["lifecycleStatus"], "READY_FOR_ASP")
        self.assertEqual(result["transaction"]["sourceSystem"], "SAP S/4HANA")
        self.assertFalse(result["transaction"]["aspStatus"] == "SUBMITTED")

    def test_sap_accounting_posting_does_not_create_a_second_invoice(self):
        opera = self.store.ingest_request("ten_five", {"fixture": "opera-b2b"}, "integrator", self.meta)
        before = len(self.store.data["transactions"])
        posted = self.store.ingest_request("ten_five", {"fixture": "sap-accounting-ref"}, "integrator", self.meta)
        self.assertEqual(posted["duplicate"], "CROSS_SYSTEM_DUPLICATE")
        self.assertFalse(posted["regulatoryInvoiceCreated"])
        self.assertEqual(posted["transactionId"], opera["transactionId"])
        self.assertEqual(len(self.store.data["transactions"]), before)

    def test_missing_buyer_fails_compliance_and_creates_an_exception(self):
        result = self.store.ingest_request("ten_five", {"fixture": "opera-missing-buyer"}, "integrator", self.meta)
        self.assertEqual(result["lifecycleStatus"], "COMPLIANCE_FAILED")
        self.assertEqual(result["transaction"]["aspStatus"], "NOT_SUBMITTED")
        exceptions = [item for item in self.store.data["exceptions"] if item.get("transactionId") == result["transactionId"]]
        self.assertTrue(any(item["category"] == "COMPLIANCE_ERROR" for item in exceptions))

    def test_unknown_property_stops_before_compliance(self):
        result = self.store.ingest_request("ten_five", {
            "sourceSystem": "OPERA",
            "sourceDocumentId": "OP-UNKNOWN",
            "payload": {"FOLIO_NO": "OP-UNKNOWN", "PROPERTY_CODE": "NO-SUCH", "DOCUMENT_SCOPE": "B2B", "BILLINGDATE": "20261014", "CURRENCY": "AED", "NETAMOUNT": "10.00"},
        }, "integrator", self.meta)
        self.assertEqual(result["lifecycleStatus"], "ENTITY_RESOLUTION_FAILED")
        self.assertFalse(result["transaction"].get("workbench"))

    def test_ambiguous_property_mapping_stops(self):
        self.store.data["entityResolution"].append({
            "id": "res_extra", "tenantId": "ten_five", "sourceSystem": "OPERA", "keyType": "OPERA_PROPERTY_CODE",
            "keyValue": "OP-DEMO", "legalEntityId": "le_five_palm", "active": True,
        })
        result = self.store.ingest_request("ten_five", {"fixture": "opera-b2b"}, "integrator", self.meta)
        self.assertEqual(result["lifecycleStatus"], "ENTITY_RESOLUTION_AMBIGUOUS")

    def test_guest_bill_is_out_of_scope(self):
        result = self.store.ingest_request("ten_five", {"fixture": "opera-b2c"}, "integrator", self.meta)
        self.assertEqual(result["lifecycleStatus"], "OUT_OF_SCOPE")
        self.assertFalse(result["regulatoryInvoiceCreated"])

    def test_changed_payload_versions_the_same_transaction(self):
        first = self.store.ingest_request("ten_five", {"fixture": "opera-b2b"}, "integrator", self.meta)
        payload = self.store.data["fixtures"][0]["payload"]
        changed = {**payload, "NETAMOUNT": "1100.00", "TAXAMOUNT": "55.00", "GROSSAMOUNT": "1155.00"}
        second = self.store.ingest_request("ten_five", {
            "sourceSystem": "OPERA", "sourceDocumentId": "OP-B2B-1001", "payload": changed,
        }, "integrator", self.meta)
        self.assertEqual(second["transactionId"], first["transactionId"])
        self.assertEqual(second["transaction"]["version"], 2)
        self.assertEqual(second["transaction"]["versions"][0]["sourcePayload"]["NETAMOUNT"], "1000.00")

    def test_another_tenant_cannot_use_the_sample_or_see_buyers(self):
        with self.assertRaises(PermissionError):
            self.store.ingest_request("ten_harbour", {"fixture": "opera-b2b"}, "integrator", self.meta)
        view = self.store.onboarding_views("ten_harbour")
        self.assertEqual(view["buyers"], [])
        self.assertTrue(all(item["id"] != "le_five_jv" for item in view["entities"]))

    def test_entity_register_has_configuration_required_slots(self):
        view = self.store.onboarding_views("ten_five")
        self.assertGreaterEqual(len(view["entities"]), 23)
        self.assertTrue(any(item["readiness"] == "CONFIGURATION REQUIRED" and item["trn"] == "CONFIGURATION REQUIRED" for item in view["entities"]))

    def test_connector_run_is_idempotent_and_makes_no_live_call(self):
        outcome = self.store.run_connector("ten_five", "con_opera", "integrator", self.meta)
        again = self.store.run_connector("ten_five", "con_opera", "integrator", self.meta)
        self.assertEqual(outcome["count"], 1)
        self.assertEqual(again["count"], 0)
        self.assertIn("No live OPERA", outcome["message"])
        self.assertFalse(any("taxilla" in str(item).lower() for item in outcome["transactions"]))


if __name__ == "__main__":
    unittest.main()
