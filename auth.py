import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, current_app


# ==========================
# FUNÇÃO PARA GERAR TOKEN JWT
# ==========================
def gerar_token(usuario):
    """
    Gera um token JWT com tempo de expiração.

    Parâmetro:
    - usuario: nome do usuário autenticado

    Retorno:
    - token JWT assinado com a SECRET_KEY da aplicação
    """
    payload = {
        "usuario": usuario,
        "perfil": "adm",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)
    }

    token = jwt.encode(
        payload,
        current_app.config["SECRET_KEY"],
        algorithm="HS256"
    )

    return token




# ==========================
# DECORATOR PARA PROTEGER ROTAS
# ==========================
def token_obrigatorio(func):
    """
    Decorator que exige um token JWT válido ou uma sessão de administrador para acessar a rota.
    """
    @wraps(func)
    def verificar_token(*args, **kwargs):
        # 1. Permite acesso se o administrador já tiver sessão ativa
        if session.get('role') == 'adm':
            return func(*args, **kwargs)

        # 2. Senão, verifica se o cabeçalho Authorization JWT foi enviado
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return jsonify({"erro": "Token ausente. Faça login."}), 401

        partes = auth_header.split()

        if len(partes) != 2 or partes[0] != "Bearer":
            return jsonify({"erro": "Cabeçalho Authorization inválido."}), 401

        token = partes[1]

        try:
            dados_token = jwt.decode(
                token,
                current_app.config["SECRET_KEY"],
                algorithms=["HS256"]
            )
            request.usuario_logado = dados_token
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "Token expirado. Faça login novamente."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido."}), 401

        return func(*args, **kwargs)

    return verificar_token