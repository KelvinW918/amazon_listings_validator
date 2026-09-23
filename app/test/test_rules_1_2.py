import pytest
from app.services.validator import validate_title, validate_listing_status

@pytest.mark.parametrize("n,override,expected", [
    (0, None, "NOK"),
    (49, None, "NOK"),
    (50, None, "OK"),
    (80, None, "OK"),
    (81, None, "NOK"),
    (81, 200, "OK"),
    (200, 200, "OK"),
    (201, 200, "NOK"),
])
def test_title_boundaries(n, override, expected):
    assert validate_title("a" * n, override)[0] == expected

def test_title_strip():
    # 48 caracteres reales + espacios al final deben fallar
    assert validate_title("a" * 48 + "  ")[0] == "NOK"

@pytest.mark.parametrize("val,expected", [
    (True, "Activo"),
    (False, "Inactivo"),
    ("Activo", "Activo"),
    ("inactivo", "Inactivo"),
    ("BUYABLE", "Activo"),
    (None, "N/A"),
])
def test_status(val, expected):
    assert validate_listing_status(val)[0] == expected