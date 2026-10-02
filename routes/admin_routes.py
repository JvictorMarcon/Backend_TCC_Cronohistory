"""Rotas específicas do painel administrativo: estatísticas e listagem de alunos."""
from functools import wraps
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from helpers import supabase, dados_eventos

admin_bp = Blueprint('admin', __name__)


# ─── decorator de proteção admin (checa a sessão, não o JWT) ────────────────
def requer_sessao_adm(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'adm':
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


@admin_bp.route('/admin/usuarios', methods=['GET'])
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
