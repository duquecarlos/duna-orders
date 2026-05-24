from __future__ import annotations


from duna_orders.parsing.anthropic_parser import AnthropicParser, _normalize_parser_payload

def test_normalize_parser_payload_lowercases_known_payment_method() -> None:
    parsed = {
        "request": {
            "payment_method": "Nequi",
            "fulfillment_type": "Domicilio",
            "items": [],
        },
        "warnings": [],
    }

    normalized = _normalize_parser_payload(parsed)

    assert normalized["request"]["payment_method"] == "nequi"
    assert normalized["request"]["fulfillment_type"] == "delivery"

def test_normalize_parser_payload_contract_for_common_llm_quirks():
    payload = {
        "request": {
            "tenant_id": " demo_tenant ",
            "customer_name": "  ",
            "fulfillment_type": " Delivery ",
            "delivery_address": "  Calle 134 #15-23 apto 502  ",
            "payment_method": " Nequi ",
            "customer_notes": "",
            "items": [
                {
                    "product_name": " Bandeja paisa ",
                    "quantity": 1,
                    "modifications": [" sin chicharrón ", ""],
                }
            ],
        }
    }

    normalized = _normalize_parser_payload(payload)
    request = normalized["request"]

    assert request["tenant_id"] == "demo_tenant"
    assert request["customer_name"] == ""
    assert request["fulfillment_type"] == "delivery"
    assert request["delivery_address"] == "Calle 134 #15-23 apto 502"
    assert request["payment_method"] == "nequi"
    assert request["customer_notes"] is None
    assert request["items"][0]["product_name"] == "Bandeja paisa"
    assert request["items"][0]["modifications"] == ["sin chicharrón"]

def test_normalize_parser_payload_maps_common_aliases() -> None:
    parsed = {
        "request": {
            "payment_method": "cash",
            "fulfillment_type": "para recoger",
            "items": [],
        },
        "warnings": [],
    }

    normalized = _normalize_parser_payload(parsed)

    assert normalized["request"]["payment_method"] == "efectivo"
    assert normalized["request"]["fulfillment_type"] == "pickup"