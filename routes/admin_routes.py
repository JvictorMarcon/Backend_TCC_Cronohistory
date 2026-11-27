"""Rotas específicas do painel administrativo: estatísticas e listagem de alunos."""
from functools import wraps
from collections import Counter
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from helpers import (
    supabase, dados_eventos, tabela_usuarios, gerar_hash_senha,
    marcar_aviso_senha, usuario_reservado, invalidar_cache, normalizar_chave, cache_get,
)
from auth import gerar_senha_temporaria, admin_autenticado

admin_bp = Blueprint('admin', __name__)


# ─── decorator de proteção admin (cookie de sessão OU token do administrador) ──
def requer_sessao_adm(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not admin_autenticado():
            return jsonify({"status": "error", "message": "Acesso restrito ao administrador."}), 403
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/admin/stats', methods=['GET'])
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
        usuarios_resp = supabase.table(tabela_usuarios()).select('*').execute()

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


PERFIS_ADMIN = ("adm", "admin", "administrador")


def _conta_admin(linha):
    """Contas administrativas não podem ser redefinidas nem excluídas por este painel."""
    perfil = str(linha.get('perfil') or linha.get('role') or '').strip().lower()
    return perfil in PERFIS_ADMIN or usuario_reservado(linha.get('user'))


def _linha_por_id(uid):
    res = supabase.table(tabela_usuarios()).select('*').eq('id', uid).limit(1).execute()
    return res.data[0] if res.data else None


@admin_bp.route('/admin/usuarios', methods=['GET'])
@requer_sessao_adm
def admin_usuarios():
    """Lista todos os usuários com detalhes para o painel do administrador (nunca devolve a senha)."""
    try:
        resp = supabase.table(tabela_usuarios()).select('*').execute()
        linhas = resp.data if resp and resp.data else []
        usuarios = [{
            "id": u.get('id'),
            "user": u.get('user'),
            "nome": u.get('nome'),
            "email": u.get('email'),
            "perfil": u.get('perfil') or u.get('role') or 'aluno',
            "fase_jogo": u.get('fase_jogo') if u.get('fase_jogo') is not None else u.get('fase_quiz'),
            "created_at": u.get('created_at'),
            "senha_redefinida_em": u.get('senha_redefinida_em'),
            "protegido": _conta_admin(u),
        } for u in linhas]

        # Ordena por data de cadastro mais recente
        usuarios.sort(key=lambda u: u.get('created_at') or '', reverse=True)

        return jsonify({"status": "success", "usuarios": usuarios, "total": len(usuarios)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro ao listar usuários: {str(e)}"}), 500


@admin_bp.route('/admin/usuarios/<int:uid>/redefinir-senha', methods=['POST'])
@requer_sessao_adm
def admin_redefinir_senha(uid):
    """Gera uma senha temporária aleatória, mostra UMA vez ao administrador e liga o aviso
    obrigatório de troca na tela da pessoa."""
    try:
        alvo = _linha_por_id(uid)
        if not alvo:
            return jsonify({"status": "error", "message": "Usuário não encontrado."}), 404
        if _conta_admin(alvo):
            return jsonify({"status": "error", "message": "Contas de administrador não podem ser redefinidas por aqui."}), 403

        temporaria = gerar_senha_temporaria()
        supabase.table(tabela_usuarios()).update({"senha": gerar_hash_senha(temporaria)}).eq('id', uid).execute()
        aviso_ligado = marcar_aviso_senha(uid)

        return jsonify({
            "status": "success",
            "message": "Senha redefinida.",
            "usuario": {"id": alvo.get('id'), "user": alvo.get('user'), "nome": alvo.get('nome')},
            "senha_temporaria": temporaria,
            # False = a senha mudou, mas a coluna do aviso ainda não existe no banco (falta o SQL 002)
            "notificacao_usuario": aviso_ligado,
        }), 200
    except Exception as e:
        print("admin_redefinir_senha:", e, flush=True)
        return jsonify({"status": "error", "message": "Não foi possível redefinir a senha agora."}), 500


@admin_bp.route('/admin/usuarios/<int:uid>', methods=['DELETE'])
@requer_sessao_adm
def admin_excluir_usuario(uid):
    """Exclui um usuário. Exige repetir o nome de usuário (evita exclusão por engano via API)."""
    dados = request.get_json(silent=True) or {}
    try:
        alvo = _linha_por_id(uid)
        if not alvo:
            return jsonify({"status": "error", "message": "Usuário não encontrado."}), 404
        if _conta_admin(alvo) or str(alvo.get('id')) == str(session.get('id')):
            return jsonify({"status": "error", "message": "Contas de administrador não podem ser excluídas."}), 403
        if str(dados.get('confirmar_usuario', '')).strip() != str(alvo.get('user')):
            return jsonify({"status": "error", "message": "Confirme digitando o nome de usuário."}), 400

        # favoritos e preferências saem junto (ON DELETE CASCADE); a linha do usuário é o que importa
        supabase.table(tabela_usuarios()).delete().eq('id', uid).execute()
        invalidar_cache(f"redef:{uid}")
        return jsonify({"status": "success", "message": "Usuário excluído.",
                        "usuario": {"id": alvo.get('id'), "user": alvo.get('user')}}), 200
    except Exception as e:
        print("admin_excluir_usuario:", e, flush=True)
        return jsonify({"status": "error", "message": "Não foi possível excluir agora."}), 500


# ─── Eventos mais favoritados ────────────────────────────────────────────
def _todas_as_linhas_de_favoritos():
    """Lê a tabela inteira de favoritos em páginas (o Supabase devolve no máximo 1000 por vez)."""
    linhas, pagina, tamanho = [], 0, 1000
    while pagina < 100:  # trava de segurança: até 100 mil favoritos
        lote = supabase.table('favoritos').select('usuario_id,evento_chave').range(
            pagina * tamanho, pagina * tamanho + tamanho - 1).execute().data or []
        linhas.extend(lote)
        if len(lote) < tamanho:
            break
        pagina += 1
    return linhas


def _eventos_por_chave():
    """Nome, período e ano de cada evento, indexados pela chave normalizada."""
    lista = cache_get('eventos')
    if lista is None:
        try:
            lista = supabase.table('evento').select('nome,periodo,ano_inicio,lugar').execute().data or []
        except Exception:
            lista = []
    indice = {}
    for ev in lista:
        if ev.get('nome'):
            indice[normalizar_chave(ev['nome'])] = {
                "nome": ev.get('nome'), "periodo": ev.get('periodo') or '',
                "ano": ev.get('ano_inicio') or ev.get('ano') or '', "lugar": ev.get('lugar') or '',
            }
    # complementa com o periodos.json (rede de segurança: eventos que ainda não estão no banco)
    periodos = dados_eventos.get('periodos', []) if isinstance(dados_eventos, dict) else dados_eventos
    for periodo in periodos:
        for ev in periodo.get('acontecimentos', []):
            chave = normalizar_chave(ev.get('nome'))
            if chave and chave not in indice:
                indice[chave] = {"nome": ev.get('nome'), "periodo": periodo.get('nome') or '',
                                 "ano": ev.get('ano') or '', "lugar": ev.get('lugar') or ''}
    return indice


@admin_bp.route('/admin/favoritos', methods=['GET'])
@requer_sessao_adm
def admin_favoritos():
    """Ranking dos eventos mais favoritados. Só devolve CONTAGENS: nunca diz quem favoritou."""
    limite = min(max(request.args.get('limite', 10, type=int) or 10, 1), 200)
    try:
        linhas = _todas_as_linhas_de_favoritos()
    except Exception as e:
        texto = str(e)
        if any(t in texto for t in ('PGRST205', 'schema cache', 'does not exist', 'Could not find the table')):
            return jsonify({"status": "error", "codigo": "tabela_ausente",
                            "message": "A tabela de favoritos ainda não foi criada no banco."}), 503
        print("admin_favoritos:", e, flush=True)
        return jsonify({"status": "error", "message": "Não foi possível ler os favoritos agora."}), 500

    contagem = Counter(l.get('evento_chave') for l in linhas if l.get('evento_chave'))
    eventos = _eventos_por_chave()
    ranking = []
    for chave, total in contagem.items():
        ev = eventos.get(chave)
        ranking.append({
            "chave": chave,
            "nome": ev['nome'] if ev else chave,
            "periodo": ev['periodo'] if ev else '',
            "ano": ev['ano'] if ev else '',
            "total": total,
            "sem_evento": ev is None,  # o evento foi renomeado ou apagado depois de favoritado
        })
    ranking.sort(key=lambda r: (-r['total'], r['nome'].lower()))

    return jsonify({
        "status": "success",
        "total_favoritos": len(linhas),
        "usuarios_com_favoritos": len({l.get('usuario_id') for l in linhas}),
        "eventos_favoritados": len(contagem),
        "ranking": ranking[:limite],
    }), 200


@admin_bp.route('/admin/favoritos/usuarios', methods=['GET'])
@requer_sessao_adm
def admin_favoritos_usuarios():
    """Quem favoritou um evento (só o administrador). O ranking continua devolvendo só contagens;
    os nomes são pedidos evento a evento, quando o admin abre a linha."""
    chave = normalizar_chave(request.args.get('chave', ''))
    if not chave:
        return jsonify({"status": "error", "message": "Informe o evento (parâmetro 'chave')."}), 400
    try:
        favs = supabase.table('favoritos').select('usuario_id').eq('evento_chave', chave).execute().data or []
        ids = sorted({f['usuario_id'] for f in favs if f.get('usuario_id') is not None})
        pessoas = []
        if ids:
            todos = supabase.table(tabela_usuarios()).select('id,user,nome').execute().data or []
            por_id = {str(u.get('id')): u for u in todos}
            for uid in ids:
                u = por_id.get(str(uid))
                if u:  # usuário já excluído não aparece
                    pessoas.append({"id": u.get('id'), "user": u.get('user'), "nome": u.get('nome')})
        pessoas.sort(key=lambda p: normalizar_chave(p.get('nome') or p.get('user')))
        return jsonify({"status": "success", "chave": chave, "total": len(pessoas), "usuarios": pessoas}), 200
    except Exception as e:
        texto = str(e)
        if any(t in texto for t in ('PGRST205', 'schema cache', 'does not exist', 'Could not find the table')):
            return jsonify({"status": "error", "codigo": "tabela_ausente",
                            "message": "A tabela de favoritos ainda não foi criada no banco."}), 503
        print("admin_favoritos_usuarios:", e, flush=True)
        return jsonify({"status": "error", "message": "Não foi possível ler os favoritos agora."}), 500
