import os
import io
import json
import base64
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pypdf  # Importação padrão e segura para o interpretador
from google import genai
from google.genai import types

# Inicializa a aplicação FastAPI
app = FastAPI(title="API Extração de Notas Fiscais - Gemini Ultra-Leve")

# Inicializa o cliente do Gemini buscando a chave do Render
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class Payload(BaseModel):
    pdf_base64: str

@app.get("/")
def health_check():
    """Rota raiz para o Render validar que o serviço está online"""
    return {"status": "online", "servico": "API Extração de Notas Fiscais (Gemini)"}

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    try:
        # 1. DECODIFICAÇÃO DO BASE64
        try:
            pdf_bytes = base64.b64decode(payload.pdf_base64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro Base64: {str(e)}")
            
        # 2. EXTRAÇÃO DE TEXTO DO PDF (Usando pypdf de forma limpa)
        texto = ""
        try:
            feixe_bytes = io.BytesIO(pdf_bytes)
            leitor = pypdf.PdfReader(feixe_bytes)
            for pagina in leitor.pages:
                texto += pagina.extract_text() or ""
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Erro ao ler PDF com pypdf: {str(e)}")

        if not texto.strip():
            raise HTTPException(status_code=422, detail="O PDF está vazio ou é uma imagem escaneada.")

        # 3. CHAMADA PARA O GEMINI 1.5 FLASH
        try:
            prompt = f"Extraia as informações estruturadas desta nota fiscal e retorne estritamente um objeto JSON.\nTexto:\n{texto}"

            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "numero_nf": types.Schema(type=types.Type.STRING),
                            "fornecedor": types.Schema(type=types.Type.STRING),
                            "valor_total": types.Schema(type=types.Type.STRING),
                            "data_emissao": types.Schema(type=types.Type.STRING),
                        },
                        required=["numero_nf", "fornecedor", "valor_total", "data_emissao"],
                    ),
                ),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Falha na API do Gemini: {str(e)}")

        # 4. CONVERSÃO DO RETORNO
        try:
            resultado = json.loads(response.text.strip())
            return resultado
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro JSON: {str(e)}")

    except HTTPException as http_err:
        raise http_err
    except Exception as general_err:
        raise HTTPException(status_code=500, detail=f"Erro interno inesperado: {str(general_err)}")
