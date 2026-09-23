import pytest
from app.services.sp_api_client import sp_api_client

# HTML simulado CON video
HTML_WITH_VIDEO = """
<html>
    <body>
        <div id="imageBlock">
            <div class="vse-video-container">Player de Video</div>
        </div>
    </body>
</html>
"""

# HTML simulado SIN video
HTML_WITHOUT_VIDEO = """
<html>
    <body>
        <div id="imageBlock">
            <img src="foto1.jpg" />
            <img src="foto2.jpg" />
        </div>
    </body>
</html>
"""

def test_video_detection():
    # Probar caso CON video
    res_pos = sp_api_client._parse_video_from_html(HTML_WITH_VIDEO)
    assert res_pos is True, "Debería detectar video cuando el tag está presente"

    # Probar caso SIN video
    res_neg = sp_api_client._parse_video_from_html(HTML_WITHOUT_VIDEO)
    assert res_neg is False, "Debería retornar False cuando no hay etiquetas de video"

    print("✅ Prueba de unidad superada: La lógica de detección distingue ambos casos correctamente.")

if __name__ == "__main__":
    test_video_detection()