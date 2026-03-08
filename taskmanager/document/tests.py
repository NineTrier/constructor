import json

from django.test import TestCase


class AcceptFiltersTests(TestCase):
    def test_accept_filters_handles_missing_phrase(self):
        payload = {
            "r_1": {
                "filters": {
                    "ChangeCase": "Upper",
                },
                # phrase intentionally omitted
            }
        }
        response = self.client.post(
            "/document/acceptFilters",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content.decode("utf-8"))
        self.assertIn("r_1", body)
        self.assertEqual(body["r_1"], "")

    def test_accept_filters_handles_missing_filters(self):
        payload = {
            "r_2": {
                "phrase": "Тест",
                # filters intentionally omitted
            }
        }
        response = self.client.post(
            "/document/acceptFilters",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content.decode("utf-8"))
        self.assertEqual(body.get("r_2"), "Тест")
