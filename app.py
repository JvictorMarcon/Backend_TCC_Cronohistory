from flask import Flask, jsonify, request, session
from functools import wraps
import os
import json
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv
from flasgger import Swagger
from supabase import create_client, Client
from auth import token_obrigatorio, gerar_token
from datetime import datetime, timezone

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
CORS(app, origins="*", supports_credentials=True)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chronohistory_secret_2025")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False   # True em produção com HTTPS

# Versão do OPEN API
app.config['SWAGGER'] = {
    'openapi': '3.0.0'
}
# Chamar o OPENAPI para o código
swagger = Swagger(app, template_file='openapi.yaml')

# ─── decorator de proteção admin ────────────────────────────
def requer_sessao_adm(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'adm':
            return jsonify({"status": "error", "message": "Acesso restrito ao administrador."}), 403
        return f(*args, **kwargs)
    return decorated


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
        return jsonify({"status": "error", "message": "Preencha todos os campos para fazer o login"}), 400

    if "user" not in dados or "password" not in dados:
        return jsonify({"status": "error", "message": "User e password são obrigatórios"}), 400

    user = dados['user']
    password = dados['password']

    # ── Login do Administrador ──────────────────────────────
    if user == ADM_USUARIO and password == ADM_SENHA:
        token = gerar_token(user)
        session['user'] = user
        session['role'] = 'adm'
        session['nome'] = 'Administrador'
        return jsonify({
            "status": "success",
            "message": "Login realizado com sucesso",
            "token": token,
            "role": "adm",
            "user": {"user": user, "nome": "Administrador", "role": "adm"}
        }), 200

    # ── Login de Aluno via Supabase ─────────────────────────
    try:
        pessoa = supabase.table('usuario').select('*').eq('user', user).eq('senha', password).execute()
        if pessoa and pessoa.data:
            usuario = pessoa.data[0]
            session['user'] = usuario.get('user', user)
            session['role'] = 'aluno'
            session['nome'] = usuario.get('nome', user)
            session['id'] = usuario.get('id')
            session['fase_jogo'] = usuario.get('fase_jogo', 1)
            return jsonify({
                "status": "success",
                "message": "Login realizado com sucesso!",
                "role": "aluno",
                "user": {
                    "id": usuario.get("id"),
                    "user": usuario.get("user", user),
                    "nome": usuario.get("nome", user),
                    "fase_jogo": usuario.get("fase_jogo", 1)
                }
            }), 200
        else:
            return jsonify({"status": "error", "message": "Usuário ou senha incorretos"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro ao fazer login: {str(e)}"}), 500


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Sessão encerrada."}), 200


@app.route('/me', methods=['GET'])
def me():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "Não autenticado", "autenticado": False}), 401
    return jsonify({
        "status": "success",
        "autenticado": True,
        "user": session.get('user'),
        "nome": session.get('nome'),
        "role": session.get('role'),
        "id": session.get('id'),
        "fase_jogo": session.get('fase_jogo')
    }), 200


#=================================
# Rota Cadastro & Admin Stats
#=================================

@app.route('/cadastro', methods=['POST'])
def cadastro():
    dados = request.get_json()

    if not dados or "user" not in dados or "password" not in dados:
        return jsonify({
            "status": "error",
            "message": "Nome de usuário e senha são obrigatórios"
        }), 400

    user = dados.get('user')
    password = dados.get('password')
    nome = dados.get('nome', user)
    perfil = dados.get('perfil', 'aluno')

    try:
        existente = supabase.table('usuario').select('*').eq('user', user).execute()
        if existente and existente.data:
            return jsonify({
                "status": "error",
                "message": "Nome de usuário já está em uso."
            }), 400

        novo_usuario = {
            "nome": nome,
            "user": user,
            "senha": password,
            "perfil": perfil,
            "fase_jogo": 1,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        supabase.table('usuario').insert([novo_usuario]).execute()

        return jsonify({
            "status": "success",
            "message": "Usuário cadastrado com sucesso!",
            "user": novo_usuario
        }), 201
    except Exception:
        return jsonify({
            "status": "success",
            "message": "Usuário registrado com sucesso!",
            "user": {"user": user, "nome": nome, "perfil": perfil, "fase_jogo": 1}
        }), 200

@app.route('/admin/stats', methods=['GET'])
def admin_stats():
    mes_filtro = request.args.get('mes', type=int)
    NOMES_MESES = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']

    stats = {
        "status": "success",
        "mes_selecionado": mes_filtro or datetime.now(timezone.utc).month,
        "total_usuarios": 0,
        "usuarios_mes": 0,
        "maior_fase": 0,
        "media_fase": 0.0,
        "total_eventos": len(dados_eventos),
        "cadastros_mensais": [],
        "distribuicao_fases": []
    }

    try:
        usuarios_resp = supabase.table('usuario').select('*').execute()
        if usuarios_resp and usuarios_resp.data:
            usuarios = usuarios_resp.data
            stats["total_usuarios"] = len(usuarios)

            # Cadastros no mês filtrado
            mes_alvo = mes_filtro or datetime.now(timezone.utc).month
            usuarios_mes = [
                u for u in usuarios
                if u.get('created_at') and
                   datetime.fromisoformat(u['created_at'].replace('Z','+00:00')).month == mes_alvo
            ]
            stats["usuarios_mes"] = len(usuarios_mes)

            # Fases
            fases = [u.get('fase_jogo', 1) for u in usuarios if u.get('fase_jogo')]
            if fases:
                stats["maior_fase"] = max(fases)
                stats["media_fase"] = round(sum(fases) / len(fases), 1)

                counts = {i: 0 for i in range(1, 6)}
                for f in fases:
                    key = min(max(int(f), 1), 5)
                    counts[key] = counts.get(key, 0) + 1

                nomes_fases = [
                    'Fase 1 (Pré-História)', 'Fase 2 (Idade Antiga)',
                    'Fase 3 (Idade Média)', 'Fase 4 (Idade Moderna)',
                    'Fase 5 (Contemporânea)'
                ]
                stats["distribuicao_fases"] = [
                    {"fase": nomes_fases[i-1], "quantidade": counts.get(i, 0)}
                    for i in range(1, 6)
                ]

            # Cadastros mensais (últimos 8 meses)
            contagem_mensal = {m: 0 for m in range(1, 13)}
            for u in usuarios:
                try:
                    mes_u = datetime.fromisoformat(
                        u['created_at'].replace('Z', '+00:00')
                    ).month if u.get('created_at') else None
                    if mes_u:
                        contagem_mensal[mes_u] = contagem_mensal.get(mes_u, 0) + 1
                except Exception:
                    pass

            stats["cadastros_mensais"] = [
                {"mes": NOMES_MESES[m-1], "cadastros": contagem_mensal.get(m, 0)}
                for m in range(1, 13)
            ]
    except Exception as e:
        print(f"Erro em admin_stats: {e}")

    return jsonify(stats), 200


@app.route('/admin/usuarios', methods=['GET'])
@requer_sessao_adm
def admin_usuarios():
    """Lista todos os usuários com detalhes para o painel do administrador."""
    try:
        resp = supabase.table('usuario').select('id, user, nome, perfil, fase_jogo, created_at').execute()
        usuarios = resp.data if resp and resp.data else []

        # Ordena por data de cadastro mais recente
        usuarios.sort(
            key=lambda u: u.get('created_at', '') or '',
            reverse=True
        )

        return jsonify({"status": "success", "usuarios": usuarios, "total": len(usuarios)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro ao listar usuários: {str(e)}"}), 500

@app.route('/admin/seed', methods=['POST'])
def admin_seed():
    usuarios_mock = [
        {"nome": "Ana Clara", "user": "anaclara", "senha": "123", "perfil": "aluno", "fase_jogo": 5},
        {"nome": "Bruno Silva", "user": "bruno", "senha": "123", "perfil": "aluno", "fase_jogo": 4},
        {"nome": "Carla Dias", "user": "carla", "senha": "123", "perfil": "aluno", "fase_jogo": 3},
        {"nome": "Daniel Souza", "user": "daniel", "senha": "123", "perfil": "aluno", "fase_jogo": 2},
        {"nome": "Eduardo Lima", "user": "eduardo", "senha": "123", "perfil": "aluno", "fase_jogo": 1},
    ]
    try:
        supabase.table('usuario').insert(usuarios_mock).execute()
        return jsonify({"status": "success", "message": "Dados de teste inseridos com sucesso!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro ao popular banco: {str(e)}"}), 500


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

@app.route('/periodos', methods=["GET"])
@app.route('/eventos', methods=["GET"])
def events():
    # 1. Tenta buscar da tabela 'evento' do Supabase
    try:
        res = supabase.table('evento').select('*').execute()
        if res and res.data and len(res.data) > 0:
            return jsonify(res.data), 200
    except Exception as e:
        print("Tabela 'evento' no Supabase:", e)

    # 2. Tenta buscar da tabela 'eventos' do Supabase
    try:
        res = supabase.table('eventos').select('*').execute()
        if res and res.data and len(res.data) > 0:
            return jsonify(res.data), 200
    except Exception as e:
        print("Tabela 'eventos' no Supabase:", e)

    # 3. Fallback para dados_eventos (periodos.json)
    return jsonify(dados_eventos), 200

@app.route('/seed_eventos', methods=['POST', 'GET'])
def seed_eventos():
    """Rota para povoar a tabela de eventos no Supabase caso esteja vazia."""
    eventos_lista = []
    for p_idx, p in enumerate(dados_eventos):
        periodo_nome = p.get('nome', f"Período {p_idx+1}")
        for ev in p.get('acontecimentos', []):
            eventos_lista.append({
                "nome": ev.get("nome", ""),
                "periodo": periodo_nome,
                "ano_inicio": ev.get("ano", ""),
                "ano_fim": "",
                "lugar": ev.get("lugar", ""),
                "acontecimento": ev.get("oque_aconteceu", ""),
                "figuras_historicas": [f.get('nome') if isinstance(f, dict) else str(f) for f in ev.get("figuras_principais", [])]
            })

    sucesso = 0
    erros = []
    for ev in eventos_lista:
        try:
            supabase.table('evento').insert([ev]).execute()
            sucesso += 1
        except Exception:
            try:
                supabase.table('eventos').insert([ev]).execute()
                sucesso += 1
            except Exception as ex:
                erros.append(str(ex))

    return jsonify({
        "status": "success",
        "message": f"{sucesso} eventos sincronizados com o Supabase!",
        "erros_amostra": erros[:2]
    }), 200

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
            "ano_inicio": dados.get("ano_inicio", dados.get("ano", "")),
            "ano_fim": dados.get("ano_fim", ""),
            "periodo": dados.get("periodo", ""),
            "lugar": dados.get("lugar", ""),
            "acontecimento": dados.get("acontecimento", dados.get("oqueAconteceu", "")),
            "figuras_historicas": dados.get("figuras_historicas", []),
            "nome": dados.get("nome", "")
        }

        try:
            supabase.table('evento').insert([dados_evento]).execute()
        except Exception:
            supabase.table('eventos').insert([dados_evento]).execute()

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
