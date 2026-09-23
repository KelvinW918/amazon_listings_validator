import asyncio
import json
from app.services.sp_api_client import sp_api_client
from app.services.validator import evaluate_listing

ASIN_TEST = "B0GWYZS3CW"
MARKETPLACE = "US"

async def debug_asin():
    print(f"🔍 [1/3] Solicitando datos reales a SP-API para ASIN: {ASIN_TEST} ({MARKETPLACE})...")

    # 1. Obtener datos desde Amazon SP-API
    raw_result = await sp_api_client.fetch_catalog_item(asin=ASIN_TEST, marketplace=MARKETPLACE)

    # Convertir modelo Pydantic a diccionario de forma segura
    if hasattr(raw_result, "model_dump"):
        raw_data = raw_result.model_dump()
    elif hasattr(raw_result, "dict"):
        raw_data = raw_result.dict()
    elif isinstance(raw_result, dict):
        raw_data = raw_result
    else:
        raw_data = vars(raw_result)

    print("\n📦 [2/3] ESTRUCTURA RECIBIDA:")
    print(f" - Clase del objeto: {type(raw_result).__name__}")
    print(f" - Atributos/Campos presentes: {list(raw_data.keys())}")

    # Imprimir el contenido completo en formato JSON
    print("\n📄 Full Payload Extraído:")
    print(json.dumps(raw_data, indent=2, ensure_ascii=False, default=str))

    # 2. Evaluar con el motor de reglas
    print("\n⚖️ [3/3] RESULTADO DE EVALUACIÓN DEL MOTOR:")
    evaluation = evaluate_listing(raw_result)

    if hasattr(evaluation, "model_dump"):
        eval_dict = evaluation.model_dump()
    elif hasattr(evaluation, "dict"):
        eval_dict = evaluation.dict()
    else:
        eval_dict = evaluation

    print(json.dumps(eval_dict, indent=2, ensure_ascii=False, default=str))

    if hasattr(sp_api_client, "close"):
        await sp_api_client.close()

if __name__ == "__main__":
    asyncio.run(debug_asin())