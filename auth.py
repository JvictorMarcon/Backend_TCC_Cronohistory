import jwt
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, current_app, session


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
# CÓDIGO DE REDEFINIÇÃO DE SENHA
# ==========================
def gerar_codigo_reset():
    """
    Gera um código numérico de 6 dígitos para confirmar um pedido de
    redefinição de senha por e-mail. Usa `secrets` (não `random`) porque
    esse código funciona como uma senha de uso único — precisa vir de um
    gerador criptograficamente seguro, não de um PRNG previsível.

    O código em si não é o que fica guardado no banco: quem chama essa
    função é responsável por guardar seu hash (generate_password_hash),
    o mesmo padrão já usado para a senha do usuário.
    """
    return str(secrets.randbelow(900_000) + 100_000)


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


# ==========================
# SENHA TEMPORÁRIA (redefinição feita pelo administrador)
# ==========================
# Sem caracteres que se confundem na leitura (0/O, 1/I/L): o administrador vai
# passar esta senha para a pessoa por mensagem ou em voz alta.
_ALFABETO_SENHA_TEMP = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def gerar_senha_temporaria(tamanho=8):
    """8 caracteres sorteados com `secrets` (gerador seguro), sempre com letra e número."""
    while True:
        senha = "".join(secrets.choice(_ALFABETO_SENHA_TEMP) for _ in range(tamanho))
        if any(c.isdigit() for c in senha) and any(c.isalpha() for c in senha):
            return senha


# ==========================
# ADMINISTRADOR AUTENTICADO (sessão OU token)
# ==========================
def admin_autenticado():
    """True se a requisição vem do administrador: cookie de sessão ativo OU cabeçalho
    `Authorization: Bearer <token>` com um token válido emitido no login do admin.

    O token existe porque muitos navegadores (Safari, Brave, Firefox, modo anônimo) bloqueiam
    cookies de terceiros: o site fica na Vercel e a API no Render, então o cookie de sessão
    pode simplesmente não ser enviado. O login do admin guarda o token e o painel o envia.
    """
    if session.get('role') == 'adm':
        return True
    partes = request.headers.get("Authorization", "").split()
    if len(partes) != 2 or partes[0] != "Bearer":
        return False
    try:
        dados = jwt.decode(partes[1], current_app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.InvalidTokenError:  # expirado, adulterado ou de outra chave
        return False
    return dados.get("perfil") == "adm"
