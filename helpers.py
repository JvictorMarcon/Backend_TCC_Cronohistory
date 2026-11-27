"""
Infraestrutura compartilhada entre os blueprints: configuração, clientes
(Supabase, Gemini, Resend) e funções auxiliares usadas por mais de uma
rota. Nada aqui depende do objeto `app` do Flask — é por isso que pode
ser importado livremente pelos arquivos de rota sem gerar import circular.
"""
import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import httpx
from supabase import create_client, Client, ClientOptions
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
# Cliente HTTP próprio para o Supabase. O padrão da biblioteca é UMA conexão
# HTTP/2 compartilhada por todas as threads do servidor; sob requisições
# simultâneas essa conexão cai ("ConnectionTerminated") e as rotas devolvem
# 500/502 ou ficam penduradas. Aqui: HTTP/1.1 com um pequeno pool de conexões
# reaproveitadas (cada thread usa a sua) e timeouts curtos — uma chamada presa
# não segura mais a thread por 2 minutos.
_http_supabase = httpx.Client(
    http2=False,
    timeout=httpx.Timeout(20.0, connect=8.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
)
supabase: Client = create_client(
    SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(httpx_client=_http_supabase)
)

# O SDK do Gemini é pesado (~1 s de import e dezenas de MB de memória) e só a
# busca por IA usa. Carregá-lo sob demanda deixa o servidor subir mais rápido
# depois de dormir (Render free) e sobra memória para os workers.
_gemini_client = None
_gemini_lock = threading.Lock()


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        with _gemini_lock:
            if _gemini_client is None:
                from google import genai
                _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client

# Pequeno pool para fazer em paralelo coisas independentes (ex.: no cadastro,
# conferir usuário, conferir e-mail e calcular o hash da senha ao mesmo tempo).
# scrypt e as chamadas HTTP liberam o GIL, então ganham tempo de verdade.
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chrono")


# ─── Cache de leitura com validade curta ─────────────────────────────────
# /eventos e /imagens mudam raramente mas são lidos por todo visitante, e cada
# leitura custa uma ida ao Supabase (~0,4 s). Guardamos o resultado por alguns
# segundos; as rotas de escrita chamam invalidar_cache() para o próprio worker
# enxergar a mudança na hora (os outros workers expiram em até TTL segundos).
_cache_leitura = {}
_cache_lock = threading.Lock()
CACHE_TTL = 30


def cache_get(chave):
    item = _cache_leitura.get(chave)
    if item and time.monotonic() - item[0] < CACHE_TTL:
        return item[1]
    return None


def cache_set(chave, valor):
    with _cache_lock:
        _cache_leitura[chave] = (time.monotonic(), valor)


def invalidar_cache(*chaves):
    with _cache_lock:
        if chaves:
            for c in chaves:
                _cache_leitura.pop(c, None)
        else:
            _cache_leitura.clear()

caminho_arquivo = os.path.join(os.path.dirname(__file__), "periodos.json")

# Carrega os dados do arquivo JSON (fallback local usado quando o Supabase está indisponível)
with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
    dados_eventos = json.load(arquivo)


# Cache em memória do processo: tanto o nome da tabela de usuários quanto as
# colunas disponíveis são, na prática, fixos durante a vida do servidor (o
# schema do Supabase não muda sozinho). Sem isso, toda rota de autenticação
# (login, cadastro, esqueci-senha, resetar-senha) pagava 1-2 round-trips
# extras ao Supabase só pra "redescobrir" algo que já sabia da chamada
# anterior — essa era a maior causa isolada de lentidão nessas rotas.
_tabela_usuarios_cache = None
_colunas_cache = {}


def obter_colunas_tabela(tabela):
    """Retorna as colunas disponíveis na tabela (cacheado por tabela)."""
    if tabela in _colunas_cache:
        return _colunas_cache[tabela]
    try:
        # Tenta fazer uma query para detectar as colunas
        resultado = supabase.table(tabela).select('*').limit(1).execute()
        if resultado.data and len(resultado.data) > 0:
            colunas = list(resultado.data[0].keys())
            _colunas_cache[tabela] = colunas  # só cacheia um resultado real
            return colunas
        # Tabela ainda vazia: não cacheia (a próxima chamada pode já ter dados)
        return ['id', 'user', 'senha', 'nome']
    except Exception:
        return ['id', 'user', 'senha', 'nome']


def tabela_usuarios():
    """Retorna a tabela de usuários configurada ou a primeira tabela existente (cacheado)."""
    global _tabela_usuarios_cache
    if _tabela_usuarios_cache is not None:
        return _tabela_usuarios_cache

    candidatos = [USERS_TABLE] + [tabela for tabela in ('usuario', 'usuarios') if tabela != USERS_TABLE]
    erros = []
    for tabela in candidatos:
        try:
            supabase.table(tabela).select('user').limit(1).execute()
            _tabela_usuarios_cache = tabela
            return tabela
        except Exception as erro:
            erros.append(f'{tabela}: {erro}')
    raise RuntimeError('Nenhuma tabela de usuários acessível. Configure USERS_TABLE. ' + ' | '.join(erros))


# ─── senha: hash (Werkzeug) ──────────────────────────────────────────────
# O padrão do Werkzeug (scrypt N=32768) leva ~170ms por chamada — perceptível
# em CPU compartilhada (ex.: Render free tier), em toda tela de login/
# cadastro. N=16384 ainda está dentro da faixa que a OWASP considera segura
# pra scrypt, só que no dobro da velocidade (~70-90ms). check_password_hash
# não precisa saber disso: ela lê o método direto do hash salvo, então hashes
# antigos (gerados com o padrão anterior) continuam validando normalmente.
def gerar_hash_senha(valor):
    return generate_password_hash(valor, method="scrypt:16384:8:1")


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


def aquecer():
    """Pré-carrega o que o primeiro login/cadastro precisaria descobrir.

    Roda em segundo plano na subida do servidor: abre a conexão com o Supabase
    e preenche os caches de tabela/colunas, para o primeiro usuário não pagar
    esses round-trips."""
    try:
        obter_colunas_tabela(tabela_usuarios())
    except Exception as erro:
        print(f"Aviso: pré-aquecimento falhou ({erro})")


COLUNAS_LOGIN = ("id", "user", "senha", "nome", "perfil", "role", "fase_jogo", "fase_quiz", "senha_redefinida_em")


def colunas_login(tabela):
    """Lista só as colunas que o login usa (evita trazer hash de reset, e-mail etc.).
    Cai para '*' se as colunas ainda não são conhecidas."""
    conhecidas = _colunas_cache.get(tabela)
    if not conhecidas:
        return "*"
    escolhidas = [c for c in COLUNAS_LOGIN if c in conhecidas]
    return ",".join(escolhidas) if "senha" in escolhidas and "user" in escolhidas else "*"


def generate_history(evento):
    prompt_content = f"""
    Procure mais informações sobre o evento {evento}
    """
    from google.genai import types
    # Faz a chamada para o modelo pedindo uma resposta em JSON
    response = _gemini().models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",  # Força a saída em formato JSON
            response_schema=PERIODS_SCHEMA,       # Segue o esquema do config.py
        )
    )
    return response.text


# ─── nomes de usuário reservados ─────────────────────────────────────────
# O /login considera administrador quem tem um destes nomes de usuário. Por isso
# ninguém pode CRIAR uma conta nem TROCAR o próprio usuário para um deles.
USUARIOS_RESERVADOS = {"admin", "adm", "administrador", "cr0n0h1st0r7"}


def usuario_reservado(nome):
    nome = str(nome or "").strip().lower()
    adm_env = str(os.getenv("ADM_USUARIO") or "").strip().lower()
    return nome in USUARIOS_RESERVADOS or (adm_env != "" and nome == adm_env)


# ─── aviso "sua senha foi redefinida pelo administrador" ─────────────────
# A coluna usuario.senha_redefinida_em (migracoes/002_aviso_senha_redefinida.sql) fica
# preenchida desde que o administrador redefine a senha até a pessoa criar uma nova.
# O /me consulta isso a cada carregamento de página, então o resultado fica em cache
# por alguns segundos. Enquanto o SQL não for rodado, a coluna não existe e tudo
# responde "sem aviso" (a redefinição continua funcionando).
def senha_redefinida(uid):
    chave = f"redef:{uid}"
    em_cache = cache_get(chave)
    if em_cache is not None:
        return em_cache
    valor = False
    try:
        res = supabase.table(tabela_usuarios()).select('senha_redefinida_em').eq('id', uid).limit(1).execute()
        valor = bool(res.data and res.data[0].get('senha_redefinida_em'))
    except Exception:
        valor = False
    cache_set(chave, valor)
    return valor


def marcar_aviso_senha(uid):
    """Liga o aviso. Devolve False se a coluna ainda não existe no banco."""
    try:
        supabase.table(tabela_usuarios()).update(
            {"senha_redefinida_em": datetime.now(timezone.utc).isoformat()}
        ).eq('id', uid).execute()
    except Exception as erro:
        print("marcar_aviso_senha: coluna ausente ou erro:", erro, flush=True)
        invalidar_cache(f"redef:{uid}")
        return False
    invalidar_cache(f"redef:{uid}")
    return True


def limpar_aviso_senha(uid):
    """Desliga o aviso (a pessoa já criou uma senha nova)."""
    try:
        supabase.table(tabela_usuarios()).update({"senha_redefinida_em": None}).eq('id', uid).execute()
    except Exception:
        pass  # coluna ainda não existe: não há aviso para desligar
    invalidar_cache(f"redef:{uid}")


# ─── chave de um evento (usada nos favoritos) ────────────────────────────
# Nome sem acento, em minúsculas e sem espaços nas pontas: é o que liga o mesmo evento
# na linha do tempo, na galeria e no banco (que têm ids diferentes).
def normalizar_chave(texto):
    import unicodedata
    texto = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower().strip()
