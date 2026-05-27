import os
import io
import json
import base64
import xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any  # <-- Importado para dar flexibilidade ao payload
import pypdf
from docx import Document
from google import genai
from google.genai import types
from google.genai.errors import APIError

app = FastAPI(title="API Inteligente de Triagem e Extração de Notas/Faturas - Gemini 2.5")

# Inicializa o cliente do Gemini
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

PALAVRAS_CHAVE_VALIDACAO = [
    "nota fiscal", "nf-e", "nfe", "danfe", "nfse", "prestador", "tomador", 
    "faturamento", "emissão", "valor recebido", "fatura", "invoice", 
    "duplicata", "comprovante de cobrança", "cobrança", "recibo"
]

MESES_MAP = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
}

# --- AJUSTE NO PYDANTIC ---
class Payload(BaseModel):
    nome_arquivo: str
    pdf_base64: Any  # Mudamos para Any para aceitar se o Power Automate mandar como objeto ou string pura

@app.get("/")
def health_check():
    chave_existe = "SIM" if os.environ.get("GEMINI_API_KEY") else "NÃO"
    return {
        "status": "online", 
        "servico": "API Triagem e Extração Avançada Ativa",
        "chave_configurada": chave_existe
    }

@app.post("/extrair-nf")
def extrair_nf(payload: Payload):
    nome_arq = payload.nome_arquivo.lower()
    print(f"=== [API] Nova requisição: Arquivo [{payload.nome_arquivo}] ===")
    
    # -------------------------------------------------------------------------
    # FILTRO 1: VALIDAÇÃO DE EXTENSÃO
    # -------------------------------------------------------------------------
    extensoes_permitidas = (".pdf", ".xml", ".docx")
    if not nome_arq.endswith(extensoes_permitidas):
        print(f"[TRIAGEM] Arquivo descartado por extensão inválida: {payload.nome_arquivo}")
        raise HTTPException(
            status_code=422, 
            detail="Arquivo descartado: Extensão não permitida (Apenas PDF, XML ou DOCX)."
        )

    # -------------------------------------------------------------------------
    # DECODIFICAÇÃO DO BASE64 COM TRATAMENTO PARA POWER AUTOMATE
    # -------------------------------------------------------------------------
    try:
        raw_base64 = payload.pdf_base64
        
        # Se o Power Automate enviar como objeto estruturado, extraímos o $content
        if isinstance(raw_base64, dict):
            if "$content" in raw_base64:
                raw_base64 = raw_base64["$content"]
            elif "contentBytes" in raw_base64:
                raw_base64 = raw_base64["contentBytes"]
        
        # Garante que temos uma string para limpar
        string_base64_limpa = str(raw_base64).strip().replace("\n", "").replace("\r", "").strip()
        
        # Faz a decodificação segura do binário
        bytes_ascii = string_base64_limpa.encode('ascii', errors='ignore')
        conteudo_bytes = base64.b64decode(bytes_ascii)
        
    except Exception as e:
        print(f"[ERRO CRÍTICO BASE64] String inválida recebida: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro ao decodificar Base64: {str(e)}")

    # -------------------------------------------------------------------------
    # FLUXO AUTOMÁTICO: PROCESSAMENTO DE XML (Custo Zero de IA)
    # -------------------------------------------------------------------------
    if nome_arq.endswith(".xml"):
        print("[PROCESSAMENTO] Identificado arquivo XML. Processando nativamente...")
        try:
            string_xml = conteudo_bytes.decode('utf-8', errors='ignore').strip()
            raiz = ET.fromstring(string_xml)
            
            for elem in raiz.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]
            
            if raiz.find('.//infNfe') is None and raiz.find('.//infNFSe') is None and 'nfe' not in raiz.tag.lower():
                print(f"[TRIAGEM] XML rejeitado. Não possui estrutura fiscal: {payload.nome_arquivo}")
                raise HTTPException(status_code=422, detail="Arquivo descartado: O XML enviado não é um documento fiscal válido.")

            numero_nf = raiz.find('.//ide/nNF') or raiz.find('.//numero')
            emitente = raiz.find('.//emit/xNome') or raiz.find('.//prestadorServico/razaoSocial') or raiz.find('.//emit/xFant')
            cnpj = raiz.find('.//emit/CNPJ') or raiz.find('.//emit/CPF') or raiz.find('.//prestadorServico/identificacaoPrestador/cnpj')
            valor = raiz.find('.//vNF') or raiz.find('.//valores/valorLiquido') or raiz.find('.//vProd')
            data = raiz.find('.//dhEmi') or raiz.find('.//dataEmissao') or raiz.find('.//dEmi')

            mes_extenso = "Mês Não Identificado"
            if data is not None and data.text:
                if '-' in data.text:
                    partes_data = data.text.split('-')
                    if len(partes_data) >= 2 and partes_data[1] in MESES_MAP:
                        mes_extenso = MESES_MAP[partes_data[1]]
                elif '/' in data.text:
                    partes_barra = data.text.split('/')
                    if len(partes_barra) >= 2 and partes_barra[1] in MESES_MAP:
                        mes_extenso = MESES_MAP[partes_barra[1]]

            resultado_xml = {
                "tipo_documento": "Nota Fiscal",
                "fornecedor": emitente.text if emitente is not None else "Não encontrado",
                "cnpj_cpf_nif": cnpj.text if cnpj is not None else "Não encontrado",
                "numero_nf": numero_nf.text if numero_nf is not None else "Não encontrado",
                "data_emissao": data.text if data is not None else "Não encontrado",
                "mes_extenso": mes_extenso,
                "valor_total": valor.text if valor is not None else "0.00"
            }
            print(f"[SUCESSO LOCAL] XML extraído com sucesso: {resultado_xml}")
            return resultado_xml

        except HTTPException as http_err:
            raise http_err
        except Exception as e:
            print(f"[ERRO XML] Falha no parse do XML: {str(e)}")
            raise HTTPException(status_code=422, detail=f"Falha ao ler a estrutura do arquivo XML: {str(e)}")

    # -------------------------------------------------------------------------
    # FLUXO TEXTUAL: EXTRAÇÃO DE TEXTO PARA DOCX OU PDF (COM SUPORTE A TABELAS)
    # -------------------------------------------------------------------------
    texto_extraido = ""
    
    if nome_arq.endswith(".docx"):
        print("[PROCESSAMENTO] Extraindo texto de documento Word (.docx)...")
        try:
            doc = Document(io.BytesIO(conteudo_bytes))
            
            # 1. Extrai texto dos parágrafos comuns
            for paragrafo in doc.paragraphs:
                if paragrafo.text.strip():
                    texto_extraido += paragrafo.text + "\n"
            
            # 2. Extrai texto de dentro de tabelas (onde costumam ficar os cabeçalhos/layouts estruturados)
            for tabela in doc.tables:
                for linha in tabela.rows:
                    texto_linha = [celula.text.strip() for celula in linha.cells if celula.text.strip()]
                    if texto_linha:
                        # O dict.fromkeys remove duplicatas mantendo a ordem caso existam células mescladas
                        texto_extraido += " | ".join(list(dict.fromkeys(texto_linha))) + "\n"
                        
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Erro ao ler arquivo Word: {str(e)}")

    elif nome_arq.endswith(".pdf"):
        print("[PROCESSAMENTO] Extraindo texto de arquivo PDF...")
        try:
            leitor = pypdf.PdfReader(io.BytesIO(conteudo_bytes))
            for pagina in leitor.pages:
                texto_extraido += pagina.extract_text() or ""
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Erro ao ler arquivo PDF: {str(e)}")

    # -------------------------------------------------------------------------
    # FILTRO 2: VALIDAÇÃO TEXTUAL
    # -------------------------------------------------------------------------
    texto_analise = texto_extraido.lower()
    matches = [termo for termo in PALAVRAS_CHAVE_VALIDACAO if termo in texto_analise]
    
    if len(matches) < 2:
        print(f"[TRIAGEM] Arquivo textual rejeitado por falta de contexto fiscal ({len(matches)} termos encontrados).")
        raise HTTPException(
            status_code=422, 
            detail="Arquivo descartado: O conteúdo não condiz com uma Nota Fiscal ou Fatura legítima."
        )

    # -------------------------------------------------------------------------
    # CHAMADA GEMINI (Prompt Turbinado com Regras de Mapeamento)
    # -------------------------------------------------------------------------
    try:
        print("[GEMINI] Documento validado! Enviando texto para estruturação avançada...")
        
        prompt = (
            "Atue como um analista fiscal especialista em extração de dados de documentos brasileiros.\n"
            "Analise textualmente o documento fornecido. Classifique-o entre 'Nota Fiscal' ou 'Fatura' "
            "e extraia as informações necessárias estruturadas estritamente no formato JSON solicitado.\n\n"
            
            "DIRETRIZES CRÍTICAS PARA GARANTIR A CAPTURA:\n"
            "1. **fornecedor**: É a empresa emissora/prestadora localizada no topo absoluto do documento. "
            "Se o texto iniciar direto com o nome de uma empresa (Ex: 'Suprisul Materiais para Escritório Ltda'), "
            "este é obrigatoriamente o fornecedor. Não confunda com o cliente listado abaixo.\n"
            
            "2. **cnpj_cpf_nif**: Extraia o CNPJ que pertence ao fornecedor (Geralmente na parte superior). "
            "Caso o CNPJ venha na mesma linha que a inscrição estadual (Ex: 'CNPJ: 05.088.156/0001-90 I.E.: 77.371.923'), "
            "isole e retorne apenas o número do CNPJ limpo e formatado.\n"
            
            "3. **numero_nf**: Encontre o número de identificação do documento fiscal ou da fatura de locação. "
            "Se estiver no formato 'N°6748/26', extraia o número sequencial antes da barra ('6748').\n"
            
            "4. **data_emissao**: Capture a data de emissão expressa no texto. Caso o ano venha abreviado com dois dígitos "
            "(Ex: '07/05/26'), converta automaticamente para o formato de 4 dígitos ('07/05/2026').\n"
            
            "5. **mes_extenso**: Baseado exclusivamente na data de emissão que você localizou, determine o mês e escreva por "
            "extenso em português, com a inicial em maiúscula (Ex: 'Maio').\n"
            
            "6. **valor_total**: Localize o valor monetário final do documento com base na palavra-chave 'TOTAL' ou 'TOTAL DA FATURA'. "
            "Retorne apenas os caracteres numéricos e a vírgula/ponto do valor decimal (Ex: '1990,00').\n\n"
            
            f"Texto do documento para análise:\n{texto_extraido}"
        )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                # Mantido o Schema original idêntico para não alterar seu Power Automate
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "tipo_documento": types.Schema(type=types.Type.STRING),
                        "fornecedor": types.Schema(type=types.Type.STRING),
                        "cnpj_cpf_nif": types.Schema(type=types.Type.STRING),
                        "numero_nf": types.Schema(type=types.Type.STRING),
                        "data_emissao": types.Schema(type=types.Type.STRING),
                        "mes_extenso": types.Schema(type=types.Type.STRING),
                        "valor_total": types.Schema(type=types.Type.STRING),
                    },
                    required=["tipo_documento", "fornecedor", "cnpj_cpf_nif", "numero_nf", "data_emissao", "mes_extenso", "valor_total"],
                ),
            ),
        )
        
        resultado_ia = json.loads(response.text.strip())
        print(f"[GEMINI] Sucesso no mapeamento: {resultado_ia['fornecedor']} | Mês: {resultado_ia['mes_extenso']} | {resultado_ia['tipo_documento']}")
        return resultado_ia

    except APIError as api_err:
        if api_err.code == 429:
            print("[ALERTA COTA] Limite de requisições por minuto atingido no Gemini.")
            raise HTTPException(status_code=429, detail="Limite de requisições do Gemini atingido. Tente novamente em breve.")
        raise HTTPException(status_code=500, detail=f"Erro na API do Google: {str(api_err)}")
    except Exception as e:
        print(f"[ERRO PARSE] Falha geral: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro no processamento da IA: {str(e)}")
