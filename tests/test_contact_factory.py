"""Unit tests for the shared contact factory."""

from uuid import NAMESPACE_DNS, uuid4, uuid5

from src.pipeline._contact_factory import make_contact


def test_deterministic_id():
    ws_id = uuid4()
    c1 = make_contact(ws_id, "Alice@Example.com")
    c2 = make_contact(ws_id, "alice@example.com")
    expected = uuid5(NAMESPACE_DNS, f"{ws_id}:alice@example.com")
    assert c1.id == expected
    assert c2.id == expected


def test_account_id_passthrough():
    ws_id = uuid4()
    account_id = uuid4()
    c = make_contact(ws_id, "bob@example.com", account_id=account_id)
    assert c.account_id == account_id


def test_display_name_passthrough():
    ws_id = uuid4()
    c = make_contact(ws_id, "carol@example.com", display_name="Carol Smith")
    assert c.display_name == "Carol Smith"


def test_internal_flag():
    ws_id = uuid4()
    c_external = make_contact(ws_id, "dan@external.com")
    c_internal = make_contact(ws_id, "dan@internal.com", is_internal=True)
    assert c_external.is_internal is False
    assert c_internal.is_internal is True
