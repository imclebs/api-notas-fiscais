import os
import io
import json
import base64
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pypdf
from google import genai
from google.genai import types

app = FastAPI(title="API Extração de Notas Fiscais - Gemini Debug")

# Verificação de segurança da chave no log de inicialização
CHAVE_EXISTE = "SIM" if os.environ.get("GEMINI_API_KEY") else "NÃO (Variável vazia ou não encontrada)"
print(f"=== [SISTEMA] Variável GEMINI_API_KEY configurada no Render? {CHAVE_EXISTE} ===")

# Inicializa o cliente do Gemini
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class Payload(BaseModel):
    pdf_base64: str

@app.get("/")
def health_check():
    return {"status": "online", "servico": "API Extração de Notas Fiscais (Gemini)", "chave_configurada": CHAVE_EXISTE}

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    print("=== [API] Nova requisição recebida no endpoint /extrair-nf ===")
    try:
        # 1. DECODIFICAÇÃO DO BASE64
        try:
            print("[ETAPA 1] Decodificando a string Base64...")
            pdf_bytes = base64.b64decode(payload.pdf_base64)
            print(f"[ETAPA 1] Sucesso! Tamanho do arquivo decodificado: {len(pdf_bytes)} bytes.")
        except Exception as e:
            print(f"[ERRO ETAPA 1] Falha na decodificação Base64: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Erro Base64: {str(e)}")
            
        # 2. EXTRAÇÃO DE TEXTO DO PDF
        texto = ""
        try:
            print("[ETAPA 2] Extraindo texto com pypdf...")
            feixe_bytes = io.BytesIO(pdf_bytes)
            leitor = pypdf.PdfReader(feixe_bytes)
            for i, pagina in enumerate(leitor.pages):
                texto += pagina.extract_text() or ""
            print(f"[ETAPA 2] Sucesso! Caracteres extraídos: {len(texto)}")
        except Exception as e:
            print(f"[ERRO ETAPA 2] Falha ao ler PDF com pypdf: {str(e)}")
            raise HTTPException(status_code=422, detail=f"Erro ao ler PDF: {str(e)}")

        if not texto.strip():
            print("[ERRO ETAPA 2] O texto extraído está totalmente vazio.")
            raise HTTPException(status_code=422, detail="O PDF está vazio ou é uma imagem escaneada.")

        # 3. CHAMADA PARA O GEMINI 1.5 FLASH
        try:
            print("[ETAPA 3] Enviando texto para a API do Google Gemini...")
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
            print("[ETAPA 3] Sucesso! Gemini respondeu à requisição.")
        except Exception as e:
            print(f"[ERRO ETAPA 3] Falha crítica na chamada do Gemini: {str(general_err if 'general_err' in locals() else e)}")
            raise HTTPException(status_code=500, detail=f"Falha na API do Gemini: {str(e)}")

        # 4. CONVERSÃO DO RETORNO
        try:
            print("[ETAPA 4] Convertendo a resposta em JSON estruturado...")
            resultado = json.loads(response.text.strip())
            print(f"[ETAPA 4] Sucesso! Dados estruturados: {resultado}")
            return resultado
        except Exception as e:
            print(f"[ERRO ETAPA 4] Resposta do Gemini não era um JSON válido: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Erro JSON: {str(e)}")

    except HTTPException as http_err:
        raise http_err
    except Exception as general_err:
        print(f"[ERRO INESPERADO] Quebra geral no script Python: {str(general_err)}")
        raise HTTPException(status_code=500, detail=f"Erro interno inesperado: {str(general_err)}")
