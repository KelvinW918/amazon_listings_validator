import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/validator/evaluate"

def base(**over):
    p = {
        "asin": "B0TEST0001",
        "marketplace": "ES",
        "is_active": True,
        "title": "T" * 65,
        "bullet_points": [f"🔥 BENEFICIO {i}: texto de prueba" for i in "ABCDE"],
        "variation_asins": ["B0A", "B0B"],
        "image_urls": [f"https://x/{i}.jpg" for i in range(8)],
        "has_video": True,
        "has_a_plus": True,
        "a_plus_comparison_products_count": 4,
        "a_plus_has_price": True,
        "a_plus_has_reviews": True,
        "variant_review_counts": [10, 10],
    }
    p.update(over)
    return p

# T1: Payload base 100% válido
def test_t1_base_valida():
    res = client.post(ENDPOINT, json=base())
    assert res.status_code == 200
    data = res.json()
    assert data["Listing Status"] == "Activo"
    assert data["Titulo"] == "OK"
    assert "Cumple 100%" in data["Notas"]

# T2: Listing inactivo
def test_t2_inactivo():
    res = client.post(ENDPOINT, json=base(is_active=False))
    assert res.status_code == 200
    data = res.json()
    assert data["Listing Status"] == "Inactivo"
    assert "Listing Status" in data["Notas"]

# T3: Título límite inferior (49 caracteres -> NOK)
def test_t3_titulo_corto():
    res = client.post(ENDPOINT, json=base(title="T" * 49))
    assert res.status_code == 200
    data = res.json()
    assert data["Titulo"] == "NOK"
    assert "49" in data["Notas"]

# T4: Título límite superior por defecto (81 caracteres -> NOK)
def test_t4_titulo_largo():
    res = client.post(ENDPOINT, json=base(title="T" * 81))
    assert res.status_code == 200
    data = res.json()
    assert data["Titulo"] == "NOK"
    assert "81" in data["Notas"]

# T5: Múltiples fallos (Inactivo + Título 30 caracteres)
def test_t5_multiples_fallos():
    res = client.post(ENDPOINT, json=base(is_active=False, title="T" * 30))
    assert res.status_code == 200
    data = res.json()
    assert data["Listing Status"] == "Inactivo"
    assert data["Titulo"] == "NOK"
    assert "|" in data["Notas"]

# T6: Estado inválido (Pydantic rechaza tipos incorrectos)
def test_t6_estado_invalido():
    res = client.post(ENDPOINT, json=base(is_active="pausado"))
    assert res.status_code == 422

# T7: Estado ausente / None (Fallback N/A)
def test_t7_sin_estado():
    res = client.post(ENDPOINT, json=base(is_active=None))
    assert res.status_code == 200
    data = res.json()
    assert data["Listing Status"] == "N/A"