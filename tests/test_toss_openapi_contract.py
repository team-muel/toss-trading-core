import hashlib
import json
import unittest

from scripts.check_toss_openapi import verify_openapi_document


def contract_fixture() -> tuple[dict, dict, bytes]:
    document = {
        "info": {"version": "1.2.14"},
        "paths": {
            "/api/v1/accounts": {"get": {}},
            "/api/v1/conditional-orders": {"get": {}, "post": {}},
        },
    }
    body = json.dumps(document, sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    policy = {
        "runtime": {
            "toss_openapi_schema_version": "1.2.14",
            "toss_openapi_schema_hash": digest,
            "toss_openapi_schema_hash_source": "https://example.invalid/openapi.json",
            "broker_capabilities": {
                "standard_order_entry": {"enabled": False},
                "conditional_order_entry": {"enabled": False},
                "conditional_order_modify": {"enabled": False},
            },
        }
    }
    review = {
        "approved_version": "1.2.14",
        "approved_sha256": digest,
        "source": "https://example.invalid/openapi.json",
        "required_operations": {"/api/v1/accounts": ["get"]},
        "documented_but_disabled_operations": {
            "/api/v1/conditional-orders": ["get", "post"]
        },
    }
    return policy, review, body


class TossOpenApiContractTest(unittest.TestCase):
    def test_approved_document_and_disabled_writes_pass(self):
        policy, review, body = contract_fixture()

        self.assertEqual(verify_openapi_document(policy, review, body), [])

    def test_version_operation_and_enabled_write_drift_are_rejected(self):
        policy, review, body = contract_fixture()
        policy["runtime"]["toss_openapi_schema_version"] = "1.2.15"
        policy["runtime"]["broker_capabilities"]["conditional_order_entry"][
            "enabled"
        ] = True
        review["required_operations"]["/api/v1/missing"] = ["post"]

        errors = verify_openapi_document(policy, review, body)

        self.assertTrue(any("version" in error for error in errors))
        self.assertTrue(any("missing path" in error for error in errors))
        self.assertTrue(any("must remain disabled" in error for error in errors))

    def test_unreviewed_official_operation_is_rejected(self):
        policy, review, body = contract_fixture()
        document = json.loads(body)
        document["paths"]["/api/v1/new-endpoint"] = {"get": {}}
        changed_body = json.dumps(document, sort_keys=True).encode()
        digest = hashlib.sha256(changed_body).hexdigest()
        policy["runtime"]["toss_openapi_schema_hash"] = digest
        review["approved_sha256"] = digest

        errors = verify_openapi_document(policy, review, changed_body)

        self.assertIn("unreviewed operation GET /api/v1/new-endpoint", errors)

    def test_required_write_and_duplicate_classification_are_rejected(self):
        policy, review, body = contract_fixture()
        review["required_operations"]["/api/v1/conditional-orders"] = ["post"]

        errors = verify_openapi_document(policy, review, body)

        self.assertIn(
            "operation classified twice: POST /api/v1/conditional-orders",
            errors,
        )
        self.assertIn(
            "write operation cannot be required: POST /api/v1/conditional-orders",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
