from aigateway.pii import luhn_ok, redact, redact_content


def test_luhn_valid_and_invalid():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_redacts_email():
    out, c = redact("Contact dana.reyes@northwind-labs.example for the export.")
    assert "[REDACTED_EMAIL]" in out and "dana.reyes" not in out
    assert c["email"] == 1


def test_redacts_ssn_and_skips_invalid_area():
    out, c = redact("SSN 123-45-6789 and 000-12-3456")
    assert out.startswith("SSN [REDACTED_SSN]")
    assert "000-12-3456" in out
    assert c["ssn"] == 1


def test_redacts_card_only_when_luhn_passes():
    out, c = redact("Card 4111 1111 1111 1111, order 4111 1111 1111 1112")
    assert "[REDACTED_CARD]" in out
    assert "4111 1111 1111 1112" in out
    assert c["card"] == 1


def test_redacts_phone_formats():
    out, c = redact("Call (415) 555-0134 or +1 212.555.0199 or 646-555-0100.")
    assert c["phone"] == 3
    assert "555" not in out


def test_plain_text_untouched():
    text = "Revenue grew 12% to $4.3M in 2025 across 38 regions."
    out, c = redact(text)
    assert out == text and not c


def test_redacts_text_blocks_and_keeps_other_blocks():
    blocks = [{"type": "text", "text": "mail me at a@b.io"}, {"type": "image", "source": {"x": 1}}]
    out, c = redact_content(blocks)
    assert out[0]["text"] == "mail me at [REDACTED_EMAIL]"
    assert out[1] == blocks[1]
    assert blocks[0]["text"] == "mail me at a@b.io"  # input not mutated
