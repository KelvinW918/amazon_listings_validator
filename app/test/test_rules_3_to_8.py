import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
ENDPOINT = "/api/v1/validator/evaluate"

def base_payload(**overrides):
    payload = {
        "asin": "B0TESTFULL",
        "marketplace": "ES",
        "is_active": True,
        "title": "T" * 65,
        "bullet_points": [f"🔥 BENEFICIO {i}: Texto descriptivo de prueba" for i in "ABCDE"],
        "variation_asins": ["B0VAR1", "B0VAR2"],
        "image_urls": [f"https://img.com/{i}.jpg" for i in range(8)],
        "has_video": True,
        "has_a_plus": True,
        "a_plus_comparison_products_count": 4,
        "a_plus_has_price": True,
        "a_plus_has_reviews": True,
        "variant_review_counts": [100, 100],
    }
    payload.update(overrides)
    return payload

# REGLA 3: Bullet Points
def test_rule3_bullets_incorrect_count():
    res = client.post(ENDPOINT, json=base_payload(bullet_points=["Solo un bullet"]))
    data = res.json()
    assert data["Bullet Points"] == "NOK"
    assert "Bullet" in data["Notas"]

# REGLA 4: Variaciones
def test_rule4_variations_mismatch():
    res = client.post(ENDPOINT, json=base_payload(variation_asins=[]))
    data = res.json()
    assert data["Variaciones"] in ["NOK", "N/A"]

# REGLA 5: Imágenes
def test_rule5_images_insufficient():
    res = client.post(ENDPOINT, json=base_payload(image_urls=["https://img.com/1.jpg"]))
    data = res.json()
    assert data["Imagenes Correctas"] == "NOK"
    assert "Imágenes" in data["Notas"]

# REGLA 6: Video
def test_rule6_video_missing():
    res = client.post(ENDPOINT, json=base_payload(has_video=False))
    data = res.json()
    assert data["Video"] == "NOK"
    assert "Video" in data["Notas"]

# REGLA 7: A+ Comparativa (Fallos por tabla corta, precio ausente o reseñas ausentes)
def test_rule7_aplus_incomplete():
    # Caso A: Menos de 4 productos en tabla comparativa
    res_count = client.post(ENDPOINT, json=base_payload(a_plus_comparison_products_count=2))
    assert res_count.json()["A+ Comparativa"] == "NOK"

    # Caso B: Sin precios en la tabla
    res_price = client.post(ENDPOINT, json=base_payload(a_plus_has_price=False))
    assert res_price.json()["A+ Comparativa"] == "NOK"

# REGLA 8: Reseñas Juntas / Sincronizadas
def test_rule8_reviews_desynced():
    res = client.post(ENDPOINT, json=base_payload(variant_review_counts=[100, 42]))
    data = res.json()
    assert data["Reseñas Juntas"] == "NOK"
    assert "Reseñas" in data["Notas"]