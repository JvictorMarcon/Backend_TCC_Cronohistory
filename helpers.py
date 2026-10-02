"""
Infraestrutura compartilhada entre os blueprints: configuração, clientes
(Supabase, Gemini, Resend) e funções auxiliares usadas por mais de uma
rota. Nada aqui depende do objeto `app` do Flask — é por isso que pode
ser importado livremente pelos arquivos de rota sem gerar import circular.
"""
import os
import json
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from dotenv import load_dotenv
from supabase import create_client, Client
import resend

from config import PERIODS_SCHEMA, SYSTEM_INSTRUCTION

# Carrega as variáveis do arquivo junto ao backend, independentemente do diretório de execução.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = str(os.getenv("url") or os.getenv("SUPABASE_URL", "")).strip()
SUPABASE_KEY = str(os.getenv("key")).strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
USERS_TABLE = os.getenv("USERS_TABLE", "usuario").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "Chronohistory <onboarding@resend.dev>").strip()

resend.api_key = RESEND_API_KEY

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

# Carrega os dados do arquivo JSON (fallback local usado quando o Supabase está indisponível)
with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
    dados_eventos = json.load(arquivo)


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


# ─── senha: hash (Werkzeug) com compatibilidade para contas antigas em texto puro ───
def senha_confere(senha_armazenada, senha_informada):
    senha_armazenada = str(senha_armazenada or "")
    if senha_armazenada.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        try:
            return check_password_hash(senha_armazenada, senha_informada)
        except ValueError:
            return False
    # Conta antiga, criada antes do hashing: compara em texto puro
    return senha_armazenada == senha_informada


def _parse_timestamp(valor):
    """Converte o timestamp vindo do Supabase (string ISO) para datetime com timezone."""
    if not valor:
        return None
    texto = str(valor).replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def enviar_email_codigo_reset(email, nome, codigo):
    """Envia por e-mail o código de 6 dígitos para redefinição de senha, via Resend."""
    codigo_espacado = " ".join(codigo)  # "482917" -> "4 8 2 9 1 7", mais fácil de ler/digitar
    html = f"""
    <div style="background:#0a0910;padding:40px 20px;font-family:Georgia,'Times New Roman',serif;">
      <div style="max-width:440px;margin:0 auto;background:#15121d;border:1px solid #352e42;
                  border-radius:16px;padding:36px 32px;text-align:center;">
        <p style="color:#d9b44a;letter-spacing:2px;font-size:12px;text-transform:uppercase;
                   margin:0 0 18px;">Chronohistory</p>
        <h1 style="color:#f5efe4;font-size:22px;margin:0 0 16px;">Redefinir sua senha</h1>
        <p style="color:#bab2c9;font-size:14px;line-height:1.6;margin:0 0 24px;">
          Olá, {nome}. Use o código abaixo para criar uma nova senha.
          Ele é válido por <b style="color:#f5efe4;">15 minutos</b>.
        </p>
        <div style="display:inline-block;background:#1c1826;border:1px solid #d9b44a;
                    border-radius:10px;padding:16px 28px;margin:0 0 24px;">
          <span style="color:#d9b44a;font-size:28px;font-weight:bold;letter-spacing:6px;
                       font-family:'Courier New',monospace;">{codigo_espacado}</span>
        </div>
        <p style="color:#837c94;font-size:12px;line-height:1.6;margin:0;">
          Se você não pediu essa redefinição, pode ignorar este e-mail com segurança —
          sua senha atual continua valendo.
        </p>
      </div>
    </div>
    """
    resend.Emails.send({
        "from": EMAIL_REMETENTE,
        "to": [email],
        "subject": f"{codigo} é o seu código de redefinição — Chronohistory",
        "html": html,
    })


def generate_history(evento):
    prompt_content = f"""
    Procure mais informações sobre o evento {evento}
    """
    # Faz a chamada para o modelo pedindo uma resposta em JSON
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",  # Força a saída em formato JSON
            response_schema=PERIODS_SCHEMA,       # Segue o esquema do config.py
        )
    )
    return response.text
