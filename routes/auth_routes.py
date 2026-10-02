"""Rotas de autenticação: login, logout, sessão, cadastro e redefinição de senha."""
import os
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from auth import gerar_token, gerar_codigo_reset
from helpers import (
    supabase,
    tabela_usuarios,
    obter_colunas_tabela,
    senha_confere,
    _parse_timestamp,
    enviar_email_codigo_reset,
)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    dados = request.get_json()

    if not dados:
        return jsonify({"status": "error", "message": "Preencha todos os campos para fazer o login"}), 400

    if "user" not in dados or "password" not in dados:
        return jsonify({"status": "error", "message": "User e password são obrigatórios"}), 400

    user = str(dados['user']).strip()
    password = str(dados['password']).strip()

    # ── Login do Administrador (Direto / Credenciais Globais) ──
    adm_user_env = str(os.getenv("ADM_USUARIO")).strip()
    adm_pass_env = str(os.getenv("ADM_SENHA")).strip()

    is_adm_direct = (
        (user == adm_user_env and password == adm_pass_env)
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
        pessoa = supabase.table(tabela).select('*').eq('user', user).limit(1).execute()

        if pessoa and pessoa.data and senha_confere(pessoa.data[0].get('senha'), password):
            usuario = pessoa.data[0]

            # Conta antiga com senha em texto puro: migra para hash agora que a senha foi conferida
            if not str(usuario.get('senha', '')).startswith(("pbkdf2:", "scrypt:", "argon2:")):
                try:
                    supabase.table(tabela).update(
                        {"senha": generate_password_hash(password)}
                    ).eq('id', usuario['id']).execute()
                except Exception:
                    pass

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


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Sessão encerrada."}), 200


@auth_bp.route('/me', methods=['GET'])
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


@auth_bp.route('/cadastro', methods=['POST'])
def cadastro():
    dados = request.get_json()

    if not dados or "user" not in dados or "password" not in dados or "email" not in dados:
        return jsonify({
            "status": "error",
            "message": "Nome de usuário, e-mail e senha são obrigatórios"
        }), 400

    user = str(dados.get('user')).strip()
    password = str(dados.get('password')).strip()
    email = str(dados.get('email')).strip().lower()
    nome = str(dados.get('nome', user)).strip()

    if not user or not password or not email:
        return jsonify({
            "status": "error",
            "message": "Nome de usuário, e-mail e senha não podem estar vazios"
        }), 400

    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({
            "status": "error",
            "message": "Informe um e-mail válido"
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

        if 'email' in colunas_disponiveis:
            email_existente = supabase.table(tabela).select('id').eq('email', email).limit(1).execute()
            if email_existente.data:
                return jsonify({
                    "status": "error",
                    "message": "Este e-mail já está cadastrado. Faça login ou use outro e-mail."
                }), 400

        # Cria payload apenas com campos que existem na tabela
        payload = {
            "nome": nome,
            "user": user,
            "senha": generate_password_hash(password),
        }

        if 'email' in colunas_disponiveis:
            payload['email'] = email

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


@auth_bp.route('/esqueci-senha', methods=['POST'])
def esqueci_senha():
    dados = request.get_json()
    email = str((dados or {}).get('email', '')).strip().lower()

    # Resposta genérica sempre, exista o e-mail ou não — evita que alguém
    # descubra quais e-mails estão cadastrados testando este formulário.
    resposta_generica = jsonify({
        "status": "success",
        "message": "Se esse e-mail estiver cadastrado, você vai receber um código de redefinição em instantes."
    })

    if not email or '@' not in email:
        return resposta_generica, 200

    try:
        tabela = tabela_usuarios()
        colunas_disponiveis = obter_colunas_tabela(tabela)
        if 'email' not in colunas_disponiveis or 'reset_codigo' not in colunas_disponiveis:
            print("Aviso: colunas reset_codigo/reset_codigo_expira não existem na tabela de usuários.")
            return resposta_generica, 200

        resultado = supabase.table(tabela).select('id, nome, user, email').eq('email', email).limit(1).execute()
        if not resultado.data:
            return resposta_generica, 200

        usuario = resultado.data[0]
        codigo = gerar_codigo_reset()
        expira_em = datetime.now(timezone.utc) + timedelta(minutes=15)

        supabase.table(tabela).update({
            "reset_codigo": generate_password_hash(codigo),
            "reset_codigo_expira": expira_em.isoformat(),
        }).eq('id', usuario['id']).execute()

        enviar_email_codigo_reset(email, usuario.get('nome') or usuario.get('user'), codigo)
    except Exception as erro:
        # Não revela o erro ao cliente (evitaria vazar se o e-mail existe ou não);
        # fica registrado no log do servidor para depuração.
        print(f"Erro ao processar /esqueci-senha: {erro}")

    return resposta_generica, 200


@auth_bp.route('/resetar-senha', methods=['POST'])
def resetar_senha():
    dados = request.get_json()
    email = str((dados or {}).get('email', '')).strip().lower()
    codigo = str((dados or {}).get('codigo', '')).strip()
    nova_senha = str((dados or {}).get('password', '')).strip()

    erro_generico = jsonify({"status": "error", "message": "Código inválido ou expirado. Peça um novo código."})

    if not email or not codigo or not nova_senha:
        return jsonify({"status": "error", "message": "E-mail, código e nova senha são obrigatórios."}), 400

    if len(nova_senha) < 6:
        return jsonify({"status": "error", "message": "A senha precisa ter pelo menos 6 caracteres."}), 400

    try:
        tabela = tabela_usuarios()
        resultado = supabase.table(tabela).select('id, reset_codigo, reset_codigo_expira').eq('email', email).limit(1).execute()
        if not resultado.data:
            return erro_generico, 400

        usuario = resultado.data[0]
        codigo_hash = usuario.get('reset_codigo')
        expira_em = _parse_timestamp(usuario.get('reset_codigo_expira'))

        if not codigo_hash or not expira_em or datetime.now(timezone.utc) > expira_em:
            return erro_generico, 400

        if not check_password_hash(codigo_hash, codigo):
            return erro_generico, 400

        supabase.table(tabela).update({
            "senha": generate_password_hash(nova_senha),
            "reset_codigo": None,
            "reset_codigo_expira": None,
        }).eq('id', usuario['id']).execute()

        return jsonify({
            "status": "success",
            "message": "Senha redefinida com sucesso! Faça login com a nova senha."
        }), 200
    except Exception as erro:
        return jsonify({"status": "error", "message": f"Erro ao redefinir senha: {erro}"}), 500
