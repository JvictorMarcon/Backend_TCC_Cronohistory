from flask import Flask, jsonify, request
import os
import json
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv
from flasgger import Swagger
from supabase import create_client, Client
from auth import token_obrigatorio, gerar_token

# importando as Constantes
from config import PERIODS_SCHEMA, SYSTEM_INSTRUCTION

# Carrega as variáveis de ambiente e inicia o Gemini
load_dotenv()

SUPABASE_URL = str(os.getenv("url"))
SUPABASE_KEY = str(os.getenv("key"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADM_USUARIO = os.getenv("ADM_USUARIO")
ADM_SENHA = os.getenv("ADM_SENHA")

# Clientes
client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

caminho_arquivo = os.path.join(os.path.dirname(__file__), "periodos.json")

# Carrega os dados do arquivo JSON
with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
    dados_eventos = json.load(arquivo)

# Inicializa o Flask
app = Flask(__name__)
CORS(app, origins="*")

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# Versão do OPEN API
app.config['SWAGGER'] = {
    'openapi': '3.0.0'
}
# Chamar o OPENAPI para o código
swagger = Swagger(app, template_file='openapi.yaml')


def generate_history(evento):
    prompt_content = f"""
    Procure mais informações sobre o evento {evento}
    """
    # Faz a chamada para o modelo pedindo uma resposta em JSON
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",  # Força a saída em formato JSON
            response_schema=PERIODS_SCHEMA,       # Segue o esquema do config.py
        )
    )
    return response.text

#=================================
# Rota login
#=================================

@app.route('/login', methods=['POST'])
def login():
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha todos os campos para fazer o login"
        }), 400

    if "user" not in dados or "password" not in dados:
        return jsonify({
            "status": "error",
            "message": "User e password são obrigatórios"
        }), 400

    user = dados['user']
    password = dados['password']
    
    if user == ADM_USUARIO and password == ADM_SENHA:
        token = gerar_token(user)
        return jsonify({
            "message":"Login realizado com sucesso",
            "token": token
        }), 200

    try:
        pessoa = supabase.table('usuario').select('*').eq('user', user).eq('senha', password).execute()
        if pessoa and pessoa.data:
            usuario = pessoa.data[0]
            return jsonify({
                "status": "success",
                "message": "Login realizado com sucesso!",
                "user": {
                    "id": usuario.get("id"),
                    "user": usuario.get("user", user),
                    "password": password,
                    "fase_jogo": usuario.get("fase_jogo")
                }
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Usuário ou senha incorretos"
            }), 401
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao fazer login: {str(e)}"
        }), 500


#==================================
# Rotas de controle de dados
#==================================

@app.route('/')
def root():
    return jsonify({
        "status": "success",
        "message": "History moments API",
        "version": "1.0"
    }), 200

@app.route('/eventos', methods=["GET"])
def events():
    try:
        eventos = supabase.table('eventos').select('*').execute()
        if eventos and eventos.data:
            return jsonify(eventos.data), 200
    except Exception:
        pass
    return jsonify(dados_eventos), 200

@app.route('/eventos', methods=["POST"])
def busca_por_evento():
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Insira um evento para poder receber as informações"
        }), 400

    periodo = dados.get('periodo') or dados.get('evento')
    if not periodo:
        return jsonify({
            "status": "error",
            "message": "Insira um evento ou período para poder receber as informações"
        }), 400

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
            "status": "error",
            "message": f"Erro ao gerar os flashcards: {str(error)}"
        }), 500

@app.route('/adicionar_evento', methods=['POST'])
@token_obrigatorio
def adicionar_evento():
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha os campos para adicionar o evento"
        }), 400

    campos_obrigatorios = ['ano_inicio', 'ano_fim', 'periodo', 'lugar', 'acontecimento', 'figuras_historicas', 'nome']

    if not all(campo in dados for campo in campos_obrigatorios):
        return jsonify({
            "status": "error",
            "message": "Preencha todos os campos obrigatórios"
        }), 400

    try:
        dados_evento = {
            "ano_inicio": dados["ano_inicio"],
            "ano_fim": dados["ano_fim"],
            "periodo": dados["periodo"],
            "lugar": dados["lugar"],
            "acontecimento": dados["acontecimento"],
            "figuras_historicas": dados["figuras_historicas"],
            "nome": dados["nome"]
        }

        supabase.table('evento').insert([dados_evento]).execute()

        return jsonify({
            "status": "success",
            "message": "Evento adicionado com sucesso",
            "data": dados_evento
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao adicionar o evento: {str(e)}"
        }), 500

@app.route('/editar_evento/<int:id>', methods=["PATCH"])
@token_obrigatorio
def editar_evento(id):
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha todos os campos"
        }), 400

    campos_editaveis = ['ano_inicio', 'ano_fim', 'periodo', 'lugar', 'acontecimento', 'figuras_historicas', 'nome']

    campos_para_atualizar = {
        campo: dados[campo]
        for campo in campos_editaveis
        if campo in dados
    }

    if not campos_para_atualizar:
        return jsonify({
            "status": "error",
            "message": "Nenhum campo válido para atualizar foi fornecido"
        }), 400

    try:
        supabase.table('evento').update(campos_para_atualizar).eq('id', id).execute()

        return jsonify({
            "status": "success",
            "message": "Evento atualizado com sucesso!",
            "data": campos_para_atualizar
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao editar o evento: {str(e)}"
        }), 500

@app.route('/deletar_evento/<int:id>', methods=["DELETE"])
@token_obrigatorio
def deletar_evento(id):
    if not id:
        return jsonify({
            "status": "error",
            "message": "ID inválido"
        }), 400
    try:
        supabase.table('evento').delete().eq('id', id).execute()
        return jsonify({
            "status": "success",
            "message": "Evento deletado com sucesso!",
            "data": id
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao deletar o evento: {str(e)}"
        }), 500

@app.route('/imagens', methods=['GET'])
def buscar_imagens():
    try:
        dados_imagens = supabase.table("imagens").select("*").execute()
        if dados_imagens and dados_imagens.data is not None:
            return jsonify(dados_imagens.data), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Erro ao buscar as imagens"
            }), 502
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao buscar as imagens: {str(e)}"
        }), 502

@app.route('/imagens', methods=['POST'])
@token_obrigatorio
def adicionar_imagens():
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha os campos para adicionar a imagem"
        }), 400

    campos_obrigatorios = ['titulo', 'pintor', 'periodo', 'ano', 'contexto', 'url']

    if not all(campo in dados for campo in campos_obrigatorios):
        return jsonify({
            "status": "error",
            "message": "Preencha todos os campos obrigatórios para adicionar a imagem"
        }), 400

    try:
        dados_imagens = {
            "titulo": dados["titulo"],
            "pintor": dados["pintor"],
            "periodo": dados["periodo"],
            "ano": dados["ano"],
            "contexto": dados["contexto"],
            "url": dados["url"]
        }

        # Insere os dados na tabela imagens
        supabase.table("imagens").insert([dados_imagens]).execute()
        return jsonify({
            "status": "success",
            "message": "Imagem adicionada com sucesso",
            "data": dados_imagens
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao adicionar a imagem: {str(e)}"
        }), 500

@app.route('/imagens/<int:id>', methods=['DELETE'])
@token_obrigatorio
def deletar_imagens(id):
    if not id:
        return jsonify({
            "status": "error",
            "message": "ID inválido"
        }), 400
    try:
        supabase.table('imagens').delete().eq('id', id).execute()
        return jsonify({
            "status": "success",
            "message": "Imagem deletada com sucesso!",
            "data": id
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao deletar a imagem: {str(e)}"
        }), 500

@app.route('/imagens/<int:id>', methods=['PATCH'])
@token_obrigatorio
def editar_imagens(id):
    dados = request.get_json()

    if not dados:
        return jsonify({
            "status": "error",
            "message": "Preencha os campos"
        }), 400

    campos_editaveis = ['titulo', 'pintor', 'periodo', 'ano', 'contexto', 'url']

    campos_para_atualizar = {
        campo: dados[campo]
        for campo in campos_editaveis
        if campo in dados
    }

    if not campos_para_atualizar:
        return jsonify({
            "status": "error",
            "message": "Nenhum campo válido para atualizar foi fornecido"
        }), 400

    try:
        supabase.table('imagens').update(campos_para_atualizar).eq('id', id).execute()

        return jsonify({
            "status": "success",
            "message": "Imagem atualizada com sucesso!",
            "data": campos_para_atualizar
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Erro ao editar a imagem: {str(e)}"
        }), 500


#===============================
# Rotas de tratamento de erros
#===============================


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "status": "error",
        "message": "Página não encontrada",
        "data": str(error)
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "status": "error",
        "message": "Erro interno do servidor",
        "data": str(error)
    }), 500

# Executa o servidor local
if __name__ == "__main__":
    app.run(debug=True)
