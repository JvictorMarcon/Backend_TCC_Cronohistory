from flask import Flask, jsonify, request
import os
import json
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv
from flasgger import Swagger


# importanto as Constantes
from config import PERIODS_SCHEMA, SYSTEM_INSTRUCTION

# Carrega as variáveis de ambiente e inicia o Gemini
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

caminho_arquivo = os.path.join(os.path.dirname(__file__), "periodos.json")

# Carrega os dados do arquivo JSON
with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
    dados_periodos = json.load(arquivo)

# Inicializa o Flask
app = Flask(__name__)
CORS(app)

# Versão do OPEN API
app.config['SWAGGER'] = {
    'openapi' : '3.0.0'
}
# Chamar o OPENAPI para o código
swagger = Swagger(app, template_file = 'openapi.yaml')

def generate_history(periodo):
    prompt_content = f"""
    Procure mais informações sobre o periodo {periodo}
    """
    # Faz a chamada para o modelo pedindo uma resposta em JSON
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents = prompt_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json", # Força a saída em formato JSON
            response_schema=PERIODS_SCHEMA,       # Segue o esquema do config.py
        )
    )
    return response.text


@app.route('/')
def root():
    return jsonify({
        "status":"success",
        "message":"History moments API",
        "version": "1.0"
    }),200

@app.route('/periodos', methods = ["GET"])
def periods():
    return jsonify({
        "periodos": dados_periodos
    }),200

@app.route('/periodos', methods = ["POST"])
def busca_por_periodo():
    
    dados = request.get_json()
    
    if not dados or "periodo" not in dados:
        return jsonify({
            "status":"error",
            "message":"Insira um período para poder receber as informações"
        }),400
        
    periodo = dados['periodo']    
    try:
    # Pede para o Gemini gerar os flashcards (retorna como string JSON)
        periodo_json_string = generate_history(periodo)
        
        # Converte a string JSON em Dicionário Python para o Flask organizar a resposta
        informacoes_periodo = json.loads(periodo_json_string)
        
        return jsonify({
            "informações": informacoes_periodo
        }), 200
    
    except Exception as error:
        return jsonify({
            "status":"error",
            "message": f"Erro ao gerar os flashcards: {str(error)}"
        })
    

# Executa o sevidor local
if __name__ == "__main__":
    app.run(debug=True)