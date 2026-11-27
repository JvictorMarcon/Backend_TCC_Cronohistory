"""Rotas da conta logada: perfil (nome, usuário, e-mail), senha, favoritos e preferências do site.

Todas exigem sessão ativa (a mesma do /login). Favoritos e preferências ficam em
tabelas próprias (veja migracoes/001_favoritos_e_preferencias.sql); se o SQL
ainda não foi rodado no Supabase, essas duas respondem 503 e o site continua
funcionando só com o que está salvo no navegador.
"""
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, session

from helpers import supabase, tabela_usuarios, senha_confere, gerar_hash_senha, usuario_reservado, limpar_aviso_senha
from routes.auth_routes import NOME_REGEX, EMAIL_REGEX

perfil_bp = Blueprint('perfil', __name__)

MAX_FAVORITOS_POR_REQUISICAO = 500
FONTES_VALIDAS = (90, 100, 115, 130)
TEMAS_VALIDOS = ('claro', 'escuro')
ESPACAMENTOS_VALIDOS = ('normal', 'amplo')
USUARIO_REGEX = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

# Tentativas erradas de "senha atual" por usuário (evita adivinhar a senha de uma sessão aberta)
_falhas_senha = {}
LIMITE_FALHAS = 5
JANELA_FALHAS = 600  # segundos


def _erro(mensagem, status):
    return jsonify({"status": "error", "message": mensagem}), status


def exigir_login(func):
    @wraps(func)
    def interno(*args, **kwargs):
        if 'user' not in session:
            return _erro("Faça login para continuar.", 401)
        if not session.get('id'):
            # login direto do administrador (variáveis de ambiente) não tem linha na tabela de usuários
            return _erro("Esta conta não pode ser alterada por aqui.", 403)
        return func(*args, **kwargs)
    return interno


def _tabela_ausente(erro):
    texto = str(erro)
    return any(t in texto for t in ('PGRST205', 'PGRST204', 'schema cache', 'does not exist', 'Could not find the table'))


def _falha_banco(erro):
    if _tabela_ausente(erro):
        return jsonify({
            "status": "error",
            "codigo": "tabela_ausente",
            "message": "Este recurso ainda não foi ativado no banco de dados."
        }), 503
    print("perfil_routes: erro de banco:", erro, flush=True)
    return _erro("Não foi possível concluir agora. Tente novamente.", 500)


def _normalizar(texto):
    texto = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def _conferir_senha_atual(uid, armazenada, informada):
    """Confere a senha atual com limite de tentativas. Devolve None se estiver certa,
    ou a resposta de erro pronta para retornar."""
    agora = time.monotonic()
    fila = _falhas_senha.setdefault(uid, deque())
    while fila and agora - fila[0] > JANELA_FALHAS:
        fila.popleft()
    if len(fila) >= LIMITE_FALHAS:
        return _erro("Muitas tentativas com a senha atual errada. Aguarde alguns minutos.", 429)
    if not senha_confere(armazenada, informada):
        fila.append(agora)
        return _erro("A senha atual está incorreta.", 400)
    fila.clear()
    return None


def _linha_do_usuario(uid):
    res = supabase.table(tabela_usuarios()).select('*').eq('id', uid).limit(1).execute()
    return res.data[0] if res.data else None


# ─── Perfil: nome, usuário e e-mail ──────────────────────────────────────
@perfil_bp.route('/perfil', methods=['GET'])
@exigir_login
def ler_perfil():
    try:
        linha = _linha_do_usuario(session['id'])
    except Exception as e:
        return _falha_banco(e)
    if not linha:
        return _erro("Conta não encontrada.", 404)
    fase = linha.get('fase_jogo') if linha.get('fase_jogo') is not None else linha.get('fase_quiz')
    return jsonify({
        "status": "success",
        "user": linha.get('user'),
        "nome": linha.get('nome'),
        "email": linha.get('email'),
        "fase_jogo": fase,
        "criado_em": linha.get('created_at'),
    }), 200


@perfil_bp.route('/perfil', methods=['PATCH'])
@exigir_login
def atualizar_perfil():
    dados = request.get_json(silent=True) or {}
    uid = session['id']

    try:
        atual = _linha_do_usuario(uid)
    except Exception as e:
        return _falha_banco(e)
    if not atual:
        return _erro("Conta não encontrada.", 404)

    mudancas = {}

    if 'nome' in dados:
        nome = ' '.join(str(dados.get('nome', '')).split())
        if len(nome) < 2:
            return _erro("Informe um nome com pelo menos 2 letras.", 400)
        if len(nome) > 80:
            return _erro("O nome pode ter no máximo 80 caracteres.", 400)
        if not NOME_REGEX.match(nome):
            return _erro("O nome deve conter apenas letras e espaços.", 400)
        if nome != atual.get('nome'):
            mudancas['nome'] = nome

    if 'user' in dados:
        novo_user = str(dados.get('user', '')).strip()
        if novo_user != str(atual.get('user') or ''):
            if not USUARIO_REGEX.match(novo_user):
                return _erro("O usuário deve ter de 3 a 30 caracteres: letras, números, ponto, hífen ou sublinhado.", 400)
            if usuario_reservado(novo_user):
                return _erro("Esse nome de usuário não está disponível.", 400)
            mudancas['user'] = novo_user

    if 'email' in dados:
        novo_email = str(dados.get('email', '')).strip().lower()
        if novo_email != str(atual.get('email') or '').lower():
            if not EMAIL_REGEX.match(novo_email):
                return _erro("Informe um e-mail em um formato válido (ex.: usuario@email.com).", 400)
            mudancas['email'] = novo_email

    if not mudancas:
        return jsonify({"status": "success", "message": "Nada para alterar.",
                        "nome": atual.get('nome'), "user": atual.get('user'), "email": atual.get('email')}), 200

    # trocar o login ou o e-mail muda a forma de entrar na conta: exige a senha atual
    if 'user' in mudancas or 'email' in mudancas:
        senha = str(dados.get('senha_atual', '')).strip()
        if not senha:
            return _erro("Informe a senha atual para alterar o usuário ou o e-mail.", 400)
        erro = _conferir_senha_atual(uid, atual.get('senha'), senha)
        if erro:
            return erro

    try:
        tabela = tabela_usuarios()
        # unicidade (o banco também garante, mas assim a mensagem sai certa)
        if 'user' in mudancas:
            outros = supabase.table(tabela).select('id').eq('user', mudancas['user']).execute().data or []
            if any(str(o.get('id')) != str(uid) for o in outros):
                return _erro("Esse nome de usuário já está em uso. Escolha outro.", 409)
        if 'email' in mudancas:
            outros = supabase.table(tabela).select('id').eq('email', mudancas['email']).execute().data or []
            if any(str(o.get('id')) != str(uid) for o in outros):
                return _erro("Esse e-mail já está cadastrado em outra conta.", 409)
        supabase.table(tabela).update(mudancas).eq('id', uid).execute()
    except Exception as e:
        if 'duplicate' in str(e).lower() or '23505' in str(e):
            return _erro("Esse usuário ou e-mail já está em uso.", 409)
        return _falha_banco(e)

    if 'nome' in mudancas:
        session['nome'] = mudancas['nome']
    if 'user' in mudancas:
        session['user'] = mudancas['user']
    novo = {**atual, **mudancas}
    return jsonify({"status": "success", "message": "Perfil atualizado.",
                    "nome": novo.get('nome'), "user": novo.get('user'), "email": novo.get('email')}), 200


# ─── Senha ───────────────────────────────────────────────────────────────
@perfil_bp.route('/alterar-senha', methods=['POST'])
@exigir_login
def alterar_senha():
    dados = request.get_json(silent=True) or {}
    atual = str(dados.get('senha_atual', '')).strip()
    nova = str(dados.get('nova_senha', '')).strip()
    uid = session['id']

    if not atual or not nova:
        return _erro("Preencha a senha atual e a nova senha.", 400)
    # mesmas regras do cadastro
    if len(nova) < 8 or len(nova) > 20:
        return _erro("A nova senha deve ter entre 8 e 20 caracteres.", 400)
    if not re.search(r"[A-Za-z0-9À-ÖØ-öø-ÿ]", nova):
        return _erro("A nova senha deve conter ao menos uma letra ou um número.", 400)

    try:
        tabela = tabela_usuarios()
        res = supabase.table(tabela).select('id,senha').eq('id', uid).limit(1).execute()
        if not res.data:
            return _erro("Conta não encontrada.", 404)
        armazenada = res.data[0].get('senha')

        erro = _conferir_senha_atual(uid, armazenada, atual)
        if erro:
            return erro
        if senha_confere(armazenada, nova):
            return _erro("A nova senha precisa ser diferente da atual.", 400)

        supabase.table(tabela).update({"senha": gerar_hash_senha(nova)}).eq('id', uid).execute()
    except Exception as e:
        return _falha_banco(e)

    limpar_aviso_senha(uid)  # se o administrador tinha redefinido a senha, o aviso some
    return jsonify({"status": "success", "message": "Senha alterada com sucesso."}), 200


# ─── Favoritos ───────────────────────────────────────────────────────────
# A chave é o nome do evento sem acento e em minúsculas: o mesmo texto liga o
# evento da linha do tempo, da galeria e do periodos.json (que têm ids diferentes).
def _chaves_do_corpo(dados):
    bruto = dados.get('chaves')
    if bruto is None and dados.get('chave') is not None:
        bruto = [dados.get('chave')]
    if not isinstance(bruto, list):
        return None
    chaves = []
    for item in bruto[:MAX_FAVORITOS_POR_REQUISICAO]:
        chave = _normalizar(item)
        if chave and len(chave) <= 200 and chave not in chaves:
            chaves.append(chave)
    return chaves


@perfil_bp.route('/favoritos', methods=['GET'])
@exigir_login
def listar_favoritos():
    try:
        res = supabase.table('favoritos').select('evento_chave').eq('usuario_id', session['id']).execute()
    except Exception as e:
        return _falha_banco(e)
    return jsonify({"status": "success", "favoritos": [r['evento_chave'] for r in (res.data or [])]}), 200


@perfil_bp.route('/favoritos', methods=['POST'])
@exigir_login
def adicionar_favoritos():
    chaves = _chaves_do_corpo(request.get_json(silent=True) or {})
    if chaves is None:
        return _erro("Informe 'chave' ou 'chaves'.", 400)
    if not chaves:
        return jsonify({"status": "success", "adicionados": 0}), 200
    try:
        supabase.table('favoritos').upsert(
            [{"usuario_id": session['id'], "evento_chave": c} for c in chaves],
            on_conflict='usuario_id,evento_chave'
        ).execute()
    except Exception as e:
        return _falha_banco(e)
    return jsonify({"status": "success", "adicionados": len(chaves)}), 200


@perfil_bp.route('/favoritos', methods=['DELETE'])
@exigir_login
def remover_favoritos():
    chaves = _chaves_do_corpo(request.get_json(silent=True) or {})
    if not chaves:
        return _erro("Informe 'chave' ou 'chaves'.", 400)
    try:
        for chave in chaves:
            supabase.table('favoritos').delete().eq('usuario_id', session['id']).eq('evento_chave', chave).execute()
    except Exception as e:
        return _falha_banco(e)
    return jsonify({"status": "success", "removidos": len(chaves)}), 200


# ─── Preferências do site ────────────────────────────────────────────────
def _limpar_preferencias(bruto):
    """Só aceita campos conhecidos e valores válidos (nunca grava texto livre)."""
    limpo = {}
    if not isinstance(bruto, dict):
        return limpo
    if bruto.get('fonte') in FONTES_VALIDAS:
        limpo['fonte'] = bruto['fonte']
    if bruto.get('tema') in TEMAS_VALIDOS:
        limpo['tema'] = bruto['tema']
    if bruto.get('espacamento') in ESPACAMENTOS_VALIDOS:
        limpo['espacamento'] = bruto['espacamento']
    for chave in ('reduzirMovimento', 'particulas'):
        if isinstance(bruto.get(chave), bool):
            limpo[chave] = bruto[chave]
    return limpo


@perfil_bp.route('/preferencias', methods=['GET'])
@exigir_login
def ler_preferencias():
    try:
        res = supabase.table('preferencias').select('dados').eq('usuario_id', session['id']).limit(1).execute()
    except Exception as e:
        return _falha_banco(e)
    dados = res.data[0].get('dados') if res.data else {}
    return jsonify({"status": "success", "preferencias": _limpar_preferencias(dados)}), 200


@perfil_bp.route('/preferencias', methods=['PUT'])
@exigir_login
def salvar_preferencias():
    corpo = request.get_json(silent=True) or {}
    limpo = _limpar_preferencias(corpo.get('preferencias'))
    try:
        supabase.table('preferencias').upsert({
            "usuario_id": session['id'],
            "dados": limpo,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict='usuario_id').execute()
    except Exception as e:
        return _falha_banco(e)
    return jsonify({"status": "success", "preferencias": limpo}), 200
