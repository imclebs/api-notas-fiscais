from fastapi import FastAPI
from pydantic import BaseModel
import pdfplumber
import anthropic
import base64
import io
import json

app = FastAPI()
client = anthropic.Anthropic(api_key="SUA_CHAVE_CLAUDE_AQUI")

class Payload(BaseModel):
    pdf_base64: str

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    # Decodifica o PDF
    pdf_bytes = base64.b64decode(payload.pdf_base64)
    
    # Extrai o texto do PDF
    texto = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            texto += pagina.extract_text() or ""

    # Manda o texto para o Claude interpretar
    resposta = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Extraia as informações abaixo desta nota fiscal e retorne SOMENTE um JSON válido, sem texto adicional:

{{
  "numero_nf": "",
  "fornecedor": "",
  "valor_total": "",
  "data_emissao": ""
}}

Texto da nota fiscal:
{texto}"""
        }]
    )

    resultado = json.loads(resposta.content[0].text)
    return resultado