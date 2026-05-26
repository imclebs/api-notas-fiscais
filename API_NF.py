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

# Inicializa o cliente do Claude buscando a chave diretamente das variáveis de ambiente do Render
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

class Payload(BaseModel):
    pdf_base64: str

@app.get("/")
def health_check():
    """Rota raiz para testar se o serviço está online no navegador"""
    return {"status": "online", "servico": "API Extração de Notas Fiscais"}

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    try:
        # 1. VALIDAÇÃO E DECODIFICAÇÃO DO BASE64
        try:
            pdf_bytes = base64.b64decode(payload.pdf_base64)
        except Exception as e:
            raise HTTPException(
                status_code=400, 
                detail=f"Erro ao decodificar a string Base64 enviada pelo Power Automate: {str(e)}"
            )
            
        # 2. EXTRAÇÃO DE TEXTO DO PDF
        texto = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pagina in pdf.pages:
                    texto += pagina.extract_text() or ""
        except Exception as e:
            raise HTTPException(
                status_code=422, 
                detail=f"Erro no pdfplumber ao tentar ler o arquivo. O arquivo pode estar corrompido: {str(e)}"
            )

        # Verifica se o PDF gerou algum texto legível
        if not texto.strip():
            raise HTTPException(
                status_code=422, 
                detail="O PDF foi aberto, mas nenhum texto foi extraído. A nota pode ser uma imagem escaneada (foto)."
            )

        # 3. ENVIO DOS DADOS PARA O CLAUDE SONNET
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
            # Se a chave ANTHROPIC_API_KEY estiver errada no Render, o erro vai aparecer aqui
            raise HTTPException(
                status_code=500, 
                detail=f"Falha na autenticação ou limite da API da Anthropic (Claude): {str(e)}"
            )

        # 4. LIMPEZA E TRATAMENTO DA RESPOSTA DA IA
        texto_resposta = resposta.content[0].text.strip()
        
        # Remove possíveis blocos de código markdown (```json ... ```) se o Claude ignorar a instrução
        if texto_resposta.startswith("```"):
            linhas = texto_resposta.splitlines()
            if linhas[0].startswith("```"):
                linhas = linhas[1:]
            if lines[-1].startswith("```"):
                linhas = linhas[:-1]
            texto_resposta = "\n".join(linhas).strip()

        # 5. CONVERSÃO DA RESPOSTA EM JSON VÁLIDO
        try:
            resultado = json.loads(texto_resposta)
            return resultado
        except Exception as e:
            raise HTTPException(
                status_code=500, 
                detail=f"A IA respondeu, mas o texto não pôde ser convertido em JSON. Erro: {str(e)}. Resposta bruta: {texto_resposta}"
            )

    except HTTPException as http_err:
        # Repassa os erros que nós mesmos tratamos acima
        raise http_err
    except Exception as general_err:
        # Captura qualquer outro erro totalmente inesperado no servidor
        raise HTTPException(
            status_code=500, 
            detail=f"Erro interno inesperado no script Python: {str(general_err)}"
        )
