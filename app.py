from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import json

# Opcional: integração com Gemini (genai) e Swagger
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except Exception:
    genai = None
    types = None
    GENAI_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # dotenv é opcional; continuar sem falhar
    pass

try:
    from flasgger import Swagger
    FLASGGER_AVAILABLE = True
except Exception:
    Swagger = None
    FLASGGER_AVAILABLE = False

# Tentar importar constantes do config.py, mas falhar graciosamente
try:
    from config import PERIODS_SCHEMA, SYSTEM_INSTRUCTION
except Exception:
    PERIODS_SCHEMA = None
    SYSTEM_INSTRUCTION = None

BASE_DIR = os.path.dirname(__file__)
PERIODOS_PATH = os.path.join(BASE_DIR, 'periodos.json')

app = Flask(__name__)
CORS(app)

# Swagger/OpenAPI se disponível e se existir arquivo template
if FLASGGER_AVAILABLE:
    openapi_path = os.path.join(BASE_DIR, 'openapi.yaml')
    if os.path.exists(openapi_path):
        swagger = Swagger(app, template_file='openapi.yaml')

# Inicializar cliente Gemini apenas se chave existir e genai disponível
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
client = None
if GENAI_AVAILABLE and GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        client = None

# Carrega periodos.json
if not os.path.exists(PERIODOS_PATH):
    print('WARNING: periodos.json not found in project root. Create the file with period data.')
    dados_periodos = {"periodos": []}
else:
    with open(PERIODOS_PATH, 'r', encoding='utf-8') as f:
        dados_periodos = json.load(f)


def generate_history(periodo):
    """Gera/enriquece informações sobre um período usando Gemini quando disponível.
    Retorna uma string JSON (como no seu exemplo)."""
    if not client or not PERIODS_SCHEMA or not SYSTEM_INSTRUCTION:
        raise RuntimeError(
            'Gemini não está configurado (client/PERIODS_SCHEMA/SYSTEM_INSTRUCTION ausente)')

    prompt_content = f"""
    Procure mais informações sobre o periodo {periodo}
    """

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=PERIODS_SCHEMA,
        )
    )
    return response.text


@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "ok", "message": "Chronohistory API"}), 200


@app.route('/periodos', methods=['GET'])
def get_periodos():
    return jsonify(dados_periodos), 200


@app.route('/periodos', methods=['POST'])
def busca_por_periodo():
    body = request.get_json() or {}
    periodo_query = body.get('periodo')
    evento_global = body.get('eventoGlobalId')
    evento_id = body.get('eventoId')
    evento_nome = body.get('eventoNome')

    if not periodo_query:
        return jsonify({"status": "error", "message": "Campo 'periodo' é obrigatório"}), 400

    # encontrar periodo por nome ou id
    periodo_obj = None
    for p in dados_periodos.get('periodos', []):
        if str(p.get('id')) == str(periodo_query) or (p.get('nome') and p.get('nome').lower() == str(periodo_query).lower()):
            periodo_obj = p
            break

    if not periodo_obj:
        # tentar busca por substring
        for p in dados_periodos.get('periodos', []):
            if p.get('nome') and str(periodo_query).lower() in p.get('nome').lower():
                periodo_obj = p
                break

    if not periodo_obj:
        return jsonify({"status": "error", "message": "Período não encontrado"}), 404

    # se pedir evento específico, tentar encontrá-lo
    evento_obj = None
    if evento_global:
        parts = str(evento_global).split('-')
        if len(parts) == 2:
            pid, eid = parts
            if str(pid) == str(periodo_obj.get('id')):
                for ev in periodo_obj.get('acontecimentos', []):
                    if str(ev.get('id')) == str(eid):
                        evento_obj = ev
                        break
    if not evento_obj and evento_id:
        for ev in periodo_obj.get('acontecimentos', []):
            if str(ev.get('id')) == str(evento_id):
                evento_obj = ev
                break
    if not evento_obj and evento_nome:
        for ev in periodo_obj.get('acontecimentos', []):
            if ev.get('nome') and ev.get('nome').lower() == str(evento_nome).lower():
                evento_obj = ev
                break

    # montar resposta: retornar o período inteiro e, se houver evento, também retorná-lo
    resposta = {"periodo_unico": periodo_obj}
    if evento_obj:
        resposta['evento'] = evento_obj

    # Se o cliente solicitou enriquecimento via Gemini (apenas se configurado), tentar gerar
    enrich_requested = body.get('enrich') or body.get('enriquecer') or False
    if enrich_requested:
        try:
            periodo_json_string = generate_history(periodo_query)
            informacoes_periodo = json.loads(periodo_json_string)
            return jsonify({"informações": informacoes_periodo}), 200
        except Exception as error:
            # Não falhar inteiro — retornar dados locais e uma nota
            resposta['aviso_enriquecimento'] = str(error)

    return jsonify({"informações": resposta}), 200


if __name__ == '__main__':
    app.run(debug=True, port=5000)
