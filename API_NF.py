import os
import io
import json
import base64
import xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pypdf
from docx import Document
from google import genai
from google.genai import types
from google.genai.errors import APIError

app = FastAPI(title="API Inteligente de Triagem e Extração de Notas/Faturas - Gemini 2.5")

# Inicializa o cliente do Gemini
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Palavras-chave para validar se o documento textual é um documento fiscal ou cobrança legítima
PALAVRAS_CHAVE_VALIDACAO = [
    "nota fiscal", "nf-e", "nfe", "danfe", "nfse", "prestador", "tomador", 
    "faturamento", "emissão", "valor recebido", "fatura", "invoice", 
    "duplicata", "comprovante de cobrança", "cobrança", "recibo"
]

# Dicionário auxiliar para mapear e traduzir o mês numérico do XML para extenso
MESES_MAP = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
}

class Payload(BaseModel):
    nome_arquivo: str
    pdf_base64: str

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
    # DECODIFICAÇÃO DO BASE64 (Com limpeza de ruídos e caracteres não-ASCII)
    # -------------------------------------------------------------------------
    try:
        # 1. Remove espaços, quebras de linha (\n, \r) indesejadas do Power Automate
        string_base64_limpa = payload.pdf_base64.strip().replace("\n", "").replace("\r", "").strip()
        
        # 2. Força a conversão para bytes ASCII legítimos, ignorando caracteres corrompidos
        bytes_ascii = string_base64_limpa.encode('ascii', errors='ignore')
        
        # 3. Faz a decodificação segura do binário
        conteudo_bytes = base64.b64decode(bytes_ascii)
    except Exception as e:
        print(f"[ERRO CRÍTICO BASE64] String inválida recebida: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro ao decodificar Base64: {str(e)}")

    # -------------------------------------------------------------------------
    # FLUXO AUTOMÁTICO: PROCESSAMENTO DE XML (Custo Zero de IA - Processamento Local)
    # -------------------------------------------------------------------------
    if nome_arq.endswith(".xml"):
        print("[PROCESSAMENTO] Identificado arquivo XML. Processando nativamente...")
        try:
            string_xml = conteudo_bytes.decode('utf-8', errors='ignore').strip()
            raiz = ET.fromstring(string_xml)
            
            # Remove namespaces para facilitar o mapeamento das tags
            for elem in raiz.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]
            
            # Validação se é uma estrutura de nota válida
            if raiz.find('.//infNfe') is None and raiz.find('.//infNFSe') is None and 'nfe' not in raiz.tag.lower():
                print(f"[TRIAGEM] XML rejeitado. Não possui estrutura fiscal: {payload.nome_arquivo}")
                raise HTTPException(status_code=422, detail="Arquivo descartado: O XML enviado não é um documento fiscal válido.")

            # Extração de campos estruturados do XML
            numero_nf = raiz.find('.//ide/nNF') or raiz.find('.//numero')
            emitente = raiz.find('.//emit/xNome') or raiz.find('.//prestadorServico/razaoSocial') or raiz.find('.//emit/xFant')
            cnpj = raiz.find('.//emit/CNPJ') or raiz.find('.//emit/CPF') or raiz.find('.//prestadorServico/identificacaoPrestador/cnpj')
            valor = raiz.find('.//vNF') or raiz.find('.//valores/valorLiquido') or raiz.find('.//vProd')
            data = raiz.find('.//dhEmi') or raiz.find('.//dataEmissao') or raiz.find('.//dEmi')

            # Descobre o mês por extenso analisando nativamente a tag de data do XML
            mes_extenso = "Mês Não Identificado"
            if data is not None and data.text:
                if '-' in data.text:  # Formato padrão ISO: AAAA-MM-DD
                    partes_data = data.text.split('-')
                    if len(partes_data) >= 2 and partes_data[1] in MESES_MAP:
                        mes_extenso = MESES_MAP[partes_data[1]]
                elif '/' in data.text:  # Formato alternativo: DD/MM/AAAA
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
    # FLUXO TEXTUAL: EXTRAÇÃO DE TEXTO PARA DOCX OU PDF
    # -------------------------------------------------------------------------
    texto_extraido = ""
    
    if nome_arq.endswith(".docx"):
        print("[PROCESSAMENTO] Extraindo texto de documento Word (.docx)...")
        try:
            doc = Document(io.BytesIO(conteudo_bytes))
            for paragrafo in doc.paragraphs:
                texto_extraido += paragrafo.text + "\n"
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
    # FILTRO 2: VALIDAÇÃO TEXTUAL (Filtra assinaturas, ícones e arquivos aleatórios)
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
    # CHAMADA INTELIGENTE DO GEMINI (Controle de Cota 429 + Novos Campos com mes_extenso)
    # -------------------------------------------------------------------------
    try:
        print("[GEMINI] Documento validado! Enviando texto para estruturação...")
        prompt = (
            "Analise textualmente o documento fornecido. Classifique-o entre 'Nota Fiscal' ou 'Fatura' "
            "e extraia as informações necessárias estruturadas estritamente no formato JSON solicitado.\n"
            "Na propriedade 'mes_extenso', verifique a data de emissão encontrada no texto e escreva por extenso apenas o nome "
            "do mês correspondente em português, iniciando com letra maiúscula (exemplo: Janeiro, Fevereiro, Março, Abril, Maio, etc).\n"
            f"Texto do documento:\n{texto_extraido}"
        )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
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
