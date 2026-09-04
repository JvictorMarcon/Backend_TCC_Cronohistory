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

# Carrega as variáveis do arquivo junto ao backend, independentemente do diretório de execução.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = str(os.getenv("url") or os.getenv("SUPABASE_URL", "")).strip()
SUPABASE_KEY = str(
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("key")
    or os.getenv("SUPABASE_ANON_KEY", "")
).strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADM_USUARIO = str(os.getenv("ADM_USUARIO") or "CR0N0H1ST0R7").strip()
ADM_SENHA = str(os.getenv("ADM_SENHA") or "CR0N0H1ST0R7").strip()
USERS_TABLE = os.getenv("USERS_TABLE", "usuario").strip()
origens_padrao = [
    "https://tcc-chronohistory.vercel.app",
    "http://127.0.0.1:5502",
    "http://localhost:5502",
]
origens_configuradas = [
    origin.strip().rstrip('/')
    for origin in os.getenv("FRONTEND_ORIGINS", "").split(",")
    if origin.strip() and origin.strip() != "*"
]
FRONTEND_ORIGINS = list(dict.fromkeys(origens_configuradas + origens_padrao))

# Clientes
client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

caminho_arquivo = os.path.join(os.path.dirname(__file__), "periodos.json")

# Carrega os dados do arquivo JSON
with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
    dados_eventos = json.load(arquivo)

# Inicializa o Flask
app = Flask(__name__)
CORS(app, origins=FRONTEND_ORIGINS, supports_credentials=True)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chronohistory_secret_2025")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "None")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"

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


def obter_colunas_tabela(tabela):
    """Retorna as colunas disponíveis na tabela."""
    try:
        # Tenta fazer uma query para detectar as colunas
        resultado = supabase.table(tabela).select('*').limit(1).execute()
        if resultado.data and len(resultado.data) > 0:
            return list(resultado.data[0].keys())
        else:
            # Se a tabela estiver vazia, retorna colunas mínimas esperadas
            return ['id', 'user', 'senha', 'nome']
    except Exception:
        return ['id', 'user', 'senha', 'nome']


def tabela_usuarios():
    """Retorna a tabela de usuários configurada ou a primeira tabela existente."""
    candidatos = [USERS_TABLE] + [tabela for tabela in ('usuario', 'usuarios') if tabela != USERS_TABLE]
    erros = []
    for tabela in candidatos:
        try:
            supabase.table(tabela).select('user').limit(1).execute()
            return tabela
        except Exception as erro:
            erros.append(f'{tabela}: {erro}')
    raise RuntimeError('Nenhuma tabela de usuários acessível. Configure USERS_TABLE. ' + ' | '.join(erros))


def generate_history(evento):
    prompt_content = f"""
    Procure mais informações sobre o evento {evento}
    """
    # Faz a chamada para o modelo pedindo uma resposta em JSON
    response = client.models.generate_content(
        model="gemini-3.1-flash-Lite",
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

    user = str(dados['user']).strip()
    password = str(dados['password']).strip()

    # ── Login do Administrador (Direto / Credenciais Globais) ──
    adm_user_env = str(os.getenv("ADM_USUARIO") or "CR0N0H1ST0R7").strip()
    adm_pass_env = str(os.getenv("ADM_SENHA") or "CR0N0H1ST0R7").strip()

    is_adm_direct = (
        (user == adm_user_env and password == adm_pass_env) or
        (user == "CR0N0H1ST0R7" and password == "CR0N0H1ST0R7") or
        (user.lower() in ["admin", "adm"] and password in [adm_pass_env, "admin", "admin123", "CR0N0H1ST0R7"])
    )

    if is_adm_direct:
        token = gerar_token(user)
        session['user'] = user
        session['role'] = 'adm'
        session['nome'] = 'Administrador'
        return jsonify({
            "status": "success",
            "message": "Login de administrador realizado com sucesso",
            "token": token,
            "role": "adm",
            "user": {"user": user, "nome": "Administrador", "role": "adm"}
        }), 200

    # ── Login via Supabase ─────────────────────────────────
    try:
        tabela = tabela_usuarios()
        pessoa = supabase.table(tabela).select('*').eq('user', user).eq('senha', password).limit(1).execute()

        if pessoa and pessoa.data:
            usuario = pessoa.data[0]
            perfil_usuario = str(usuario.get("perfil") or usuario.get("role") or "").strip().lower()
            is_adm = (
                perfil_usuario in ["adm", "admin", "administrador"] or
                usuario.get("user", "").lower() in ["admin", "adm", "cr0n0h1st0r7"]
            )
            role_final = "adm" if is_adm else "aluno"

            fase_val = usuario.get("fase_jogo") if usuario.get("fase_jogo") is not None else usuario.get("fase_quiz", 1)
            session['user'] = usuario.get('user', user)
            session['role'] = role_final
            session['nome'] = usuario.get('nome', user)
            session['id'] = usuario.get('id')
            session['fase_jogo'] = fase_val
            return jsonify({
                "status": "success",
                "message": "Login realizado com sucesso!",
                "role": role_final,
                "user": {
                    "id": usuario.get("id"),
                    "user": usuario.get("user", user),
                    "nome": usuario.get("nome", user),
                    "role": role_final,
                    "fase_jogo": fase_val
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

    user = str(dados.get('user')).strip()
    password = str(dados.get('password')).strip()
    nome = str(dados.get('nome', user)).strip()
    perfil = str(dados.get('perfil', 'aluno')).strip()

    if not user or not password:
        return jsonify({
            "status": "error",
            "message": "Nome de usuário e senha não podem estar vazios"
        }), 400

    try:
        tabela = tabela_usuarios()
        existente = supabase.table(tabela).select('id').eq('user', user).limit(1).execute()
        if existente.data:
            return jsonify({
                "status": "error",
                "message": "Nome de usuário já está em uso. Escolha outro."
            }), 400

        # Detecta quais colunas existem na tabela
        colunas_disponiveis = obter_colunas_tabela(tabela)
        
        # Cria payload apenas com campos que existem na tabela
        payload = {
            "nome": nome,
            "user": user,
            "senha": password,
        }
        
        # Adiciona campos opcionais se a tabela suportar
        if 'perfil' in colunas_disponiveis:
            payload['perfil'] = 'aluno'
        
        # Tenta fase_jogo primeiro, depois fase_quiz
        if 'fase_jogo' in colunas_disponiveis:
            payload['fase_jogo'] = 1
        elif 'fase_quiz' in colunas_disponiveis:
            payload['fase_quiz'] = 1
            
        if 'created_at' in colunas_disponiveis:
            payload['created_at'] = datetime.now(timezone.utc).isoformat()

        supabase.table(tabela).insert([payload]).execute()

        return jsonify({
            "status": "success",
            "message": "Usuário cadastrado com sucesso!",
            "user": {"user": user, "nome": nome, "perfil": "aluno", "fase_jogo": 1}
        }), 201
    except Exception as erro:
        return jsonify({
            "status": "error",
            "message": f"Erro ao salvar usuário no banco de dados: {erro}"
        }), 500

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
        usuarios_resp = None
        for tbl in ['usuario', 'usuarios']:
            try:
                res = supabase.table(tbl).select('*').execute()
                if res and res.data:
                    usuarios_resp = res
                    break
            except Exception:
                pass

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
        usuarios = []
        for tbl in ['usuario', 'usuarios']:
            try:
                resp = supabase.table(tbl).select('id, user, nome, perfil, fase_jogo, created_at').execute()
                if resp and resp.data:
                    usuarios = resp.data
                    break
            except Exception:
                pass

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
                "figuras_historicas": [f.get('nome') if isinstance(f, dict) else str(f) for f in ev.get("figuras_principais", [])],
                "imagem": ev.get("imagem", "")
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
    dados = request.get_json() or {}

    nome = dados.get("nome", "").strip()
    if not nome:
        return jsonify({
            "status": "error",
            "message": "O nome do evento é obrigatório."
        }), 400

    try:
        figuras = dados.get("figuras_historicas", [])
        if isinstance(figuras, str):
            figuras = [f.strip() for f in figuras.split(",") if f.strip()]

        dados_evento = {
            "nome": nome,
            "periodo": dados.get("periodo", "Idade Contemporânea"),
            "ano_inicio": str(dados.get("ano_inicio", dados.get("ano", ""))),
            "ano_fim": str(dados.get("ano_fim", "")),
            "lugar": str(dados.get("lugar", "")),
            "acontecimento": str(dados.get("acontecimento", dados.get("oqueAconteceu", ""))),
            "figuras_historicas": figuras,
            "imagem": str(dados.get("imagem", dados.get("imagemUrl", "")))
        }

        try:
            supabase.table('evento').insert([dados_evento]).execute()
        except Exception:
            supabase.table('eventos').insert([dados_evento]).execute()

        return jsonify({
            "status": "success",
            "message": "Evento adicionado com sucesso!",
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

    campos_editaveis = ['ano_inicio', 'ano_fim', 'periodo', 'lugar', 'acontecimento', 'figuras_historicas', 'nome', 'imagem', 'imagemUrl']

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
            if len(dados_imagens.data) > 0:
                return jsonify(dados_imagens.data), 200

            # Se a tabela imagens estiver vazia, busca os registros da tabela evento no Supabase
            dados_eventos = supabase.table("evento").select("*").execute()
            if dados_eventos and dados_eventos.data and len(dados_eventos.data) > 0:
                eventos_como_imagens = []
                for ev in dados_eventos.data:
                    eventos_como_imagens.append({
                        "id": ev.get("id"),
                        "titulo": ev.get("nome"),
                        "periodo": ev.get("periodo"),
                        "ano": ev.get("ano_inicio") or ev.get("ano"),
                        "contexto": ev.get("acontecimento"),
                        "pintor": ev.get("lugar"),
                        "url": ev.get("imagemUrl") or ev.get("imagem") or ""
                    })
                return jsonify(eventos_como_imagens), 200

            return jsonify([]), 200
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
    dados = request.get_json() or {}

    titulo = dados.get("titulo", "").strip()
    url = dados.get("url", "").strip()

    if not titulo or not url:
        return jsonify({
            "status": "error",
            "message": "Título e URL da imagem são obrigatórios."
        }), 400

    try:
        dados_imagens = {
            "titulo": titulo,
            "pintor": str(dados.get("pintor", "Desconhecido")),
            "periodo": str(dados.get("periodo", "Geral")),
            "ano": str(dados.get("ano", "")),
            "contexto": str(dados.get("contexto", "")),
            "url": url
        }

        # Insere os dados na tabela imagens
        supabase.table("imagens").insert([dados_imagens]).execute()
        return jsonify({
            "status": "success",
            "message": "Imagem adicionada com sucesso!",
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
