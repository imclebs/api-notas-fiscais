import os
import io
import json
import base64
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pdfplumber
import anthropic

# Inicializa a aplicação FastAPI
app = FastAPI(title="API Extração de Notas Fiscais")

# Inicializa o cliente do Claude buscando a chave diretamente das variáveis de ambiente do sistema
# (Funciona tanto localmente com arquivo .env quanto configurado no painel do Render)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

class Payload(BaseModel):
    pdf_base64: str

@app.get("/")
def health_check():
    """Rota raiz para testar se o serviço está rodando e acessível no Render"""
    return {"status": "online", "servico": "API Extração de Notas Fiscais"}

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    # 1. Decodifica o PDF recebido do Power Automate
    try:
        pdf_bytes = base64.b64decode(payload.pdf_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="PDF em base64 inválido.")
        
    # 2. Extrai o texto do PDF usando o pdfplumber
    texto = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pagina in pdf.pages:
                texto += pagina.extract_text() or ""
    except Exception:
        raise HTTPException(status_code=422, detail="Não foi possível ler a estrutura do PDF.")

    if not texto.strip():
        raise HTTPException(status_code=422, detail="O PDF foi lido, mas nenhum texto legível foi extraído.")

    # 3. Envia o texto bruto para o Claude estruturar os dados em JSON
    try:
        resposta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Extraia as informações abaixo desta nota fiscal e retorne SOMENTE um JSON válido, sem texto adicional, sem explicações e sem blocos de código markdown:

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na comunicação com a API do Claude: {str(e)}")

    # 4. Trata e limpa a resposta de texto recebida da IA
    texto_resposta = resposta.content[0].text.strip()
    
    # Tratamento preventivo: se o Claude ignorar a instrução e colocar blocos de markdown ```json ... ```
    if texto_resposta.startswith("```"):
        # Remove as linhas de marcação do markdown
        linhas = texto_resposta.splitlines()
        if linhas[0].startswith("```"):
            linhas = linhas[1:]
        if linhas[-1].startswith("```"):
            linhas = linhas[:-1]
        texto_resposta = "\n".join(linhas).strip()

    # 5. Faz o parse da string tratada para JSON real do Python
    try:
        resultado = json.loads(texto_resposta)
    except Exception:
        raise HTTPException(
            status_code=500, 
            detail=f"A resposta retornada pela IA não pôde ser convertida em um JSON válido. Resposta bruta: {texto_resposta}"
        )

    return resultado
