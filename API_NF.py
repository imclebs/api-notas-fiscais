import os
import io
import json
import base64
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pdfplumber
from google import genai
from google.genai import types

# Inicializa a aplicação FastAPI
app = FastAPI(title="API Extração de Notas Fiscais - Gemini Free")

# Inicializa o cliente do Gemini buscando a chave de forma segura no Render
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class Payload(BaseModel):
    pdf_base64: str

@app.get("/")
def health_check():
    """Rota raiz para validar se a API está online no Render"""
    return {"status": "online", "servico": "API Extração de Notas Fiscais (Gemini)"}

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    try:
        # 1. VALIDAÇÃO E DECODIFICAÇÃO DO BASE64 enviado pelo Power Automate
        try:
            pdf_bytes = base64.b64decode(payload.pdf_base64)
        except Exception as e:
            raise HTTPException(
                status_code=400, 
                detail=f"Erro ao decodificar string Base64: {str(e)}"
            )
            
        # 2. EXTRAÇÃO DE TEXTO DO PDF UTILIZANDO PDFPLUMBER
        texto = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pagina in pdf.pages:
                    texto += pagina.extract_text() or ""
        except Exception as e:
            raise HTTPException(
                status_code=422, 
                detail=f"Erro ao abrir ou processar a estrutura do PDF: {str(e)}"
            )

        # Valida se o arquivo retornou algum caractere de texto legível
        if not texto.strip():
            raise HTTPException(
                status_code=422, 
                detail="O PDF foi aberto, mas nenhum texto foi extraído (provavelmente o arquivo é uma imagem escaneada)."
            )

        # 3. CHAMADA E DEFINIÇÃO DO PROMPT PARA O GEMINI 1.5 FLASH (GRATUITO)
        try:
            prompt = f"""Extraia as informações estruturadas desta nota fiscal e retorne estritamente um objeto JSON.
            
            Texto bruto extraído da nota fiscal:
            {texto}"""

            # O recurso de Schema força o Gemini a responder estritamente no formato esperado,
            # eliminando qualquer chance de quebra por caracteres adicionais ou markdown.
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
            raise HTTPException(
                status_code=500, 
                detail=f"Falha de comunicação ou autenticação na API do Gemini: {str(e)}"
            )

        # 4. LEITURA E CONVERSÃO DA RESPOSTA EM JSON VÁLIDO PARA O POWER AUTOMATE
        try:
            texto_resposta = response.text.strip()
            resultado = json.loads(texto_resposta)
            return resultado
        except Exception as e:
            raise HTTPException(
                status_code=500, 
                detail=f"Erro ao converter a resposta da IA em JSON válido: {str(e)}. Resposta bruta: {response.text}"
            )

    except HTTPException as http_err:
        raise http_err
    except Exception as general_err:
        raise HTTPException(
            status_code=500, 
            detail=f"Erro interno não mapeado no script Python: {str(general_err)}"
        )
